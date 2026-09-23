# -*- coding: utf-8 -*-
"""阶段③ 文本 LLM 推理：在 VLM 选中的图上做文字推理与字段补全。

设计要点：
    1) 输入：规则管线已经产出的 RuleDraft（含 byCategory 字典 / 各字段
       value/source），以及 VLM 选中的 sheet 列表（含 relevantLayers）。
    2) 把这些 sheet 的「标注聚合 + 表格行」抽取后，与规则初值一起喂给 LLM，
       让 LLM 在文字/表格交叉上做推理与修正。
    3) 严格 JSON 输出：每个 category 一行；每行 fields 是
          {value, trace}  （与前端 LlmField 完全一致）
       trace 字段简短中文，写明 source（annotation / table / geometry / llm）
       与 reasoning，便于三方对账定位。
    4) 失败兜底：LLM 异常或 JSON 解析失败时，把规则管线的初值作为最终值
       透传，trace="LLM 失败，沿用规则管线"。

依赖：
    - 复用 audit.rules.reports : SheetAnnotations / run_rules_pipeline 的抽取能力
    - Provider.chat（OpenAI 兼容 / Anthropic / Gemini）
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..llm.provider import Provider
from .rules import (
    CATEGORIES,
    CATEGORY_FIELDS,
    RuleDraftDict,
    RuleDraftRow,
    SheetAnnotations,
    _collect_sheet,
    _parse_field,
)


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# JSON 容错解析（与 vlm_select 同样策略）
# --------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _robust_json_loads(text: str) -> Any:
    text = (text or "").strip()
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    if "{" in text and "}" in text:
        l = text.find("{")
        r = text.rfind("}")
        if l != -1 and r != -1 and r > l:
            text = text[l : r + 1]
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return json.loads(text)


# --------------------------------------------------------------------------
# 抽取 sheet 的标注聚合（与 rules.py 一致）
# --------------------------------------------------------------------------


def _load_sheet_annotations(sheet_dir: str, sheet_id: str) -> SheetAnnotations:
    path = os.path.join(sheet_dir, f"{sheet_id}.dxf")
    return _collect_sheet(path, sheet_id)


# --------------------------------------------------------------------------
# 构造 prompt
# --------------------------------------------------------------------------

_FIELD_HUMAN = {
    "type": "桩型",
    "count": "数量",
    "diameter": "桩径(mm)",
    "area": "单根截面积(m²)",
    "volume": "体积(m³)",
    "group": "组号",
    "panel": "幅数",
    "length": "长度(m)",
    "width": "宽度(m)",
    "depth": "深度(m)",
    "code": "承台编号",
    "shape": "形状",
    "thickness": "厚度(m)",
}

_CATEGORY_HUMAN = {
    "retaining_pile": "围护桩",
    "bored_pile": "钻孔灌注桩",
    "diaphragm_wall": "地下连续墙",
    "cap": "承台",
}


def _format_initial_draft(rule_draft: RuleDraftDict) -> Dict[str, Any]:
    """把 RuleDraft 序列化为更易让 LLM 阅读的 dict。"""
    out: Dict[str, Any] = {}
    for cat in CATEGORIES:
        rows = rule_draft.byCategory.get(cat, [])
        if not rows:
            continue
        out[cat] = []
        for row in rows:
            fields = {}
            for fname, info in row.fields.items():
                fields[fname] = {
                    "value": info.get("value"),
                    "source": info.get("source", "") or "",
                }
            out[cat].append({"id": row.id, "fields": fields})
    return out


def _format_sheet_context(annotations: Dict[str, SheetAnnotations]) -> str:
    """把多张 sheet 的「文字+表格」聚合为 prompt 内的 context 文本。"""
    parts: List[str] = []
    for sid, ann in annotations.items():
        if not ann.texts and not ann.table_rows:
            continue
        parts.append(f"--- sheet: {sid} ({ann.sheet_name}) ---")
        if ann.texts:
            # 截断到 60 条，避免 prompt 过长
            sample = ann.texts[:60]
            parts.append("texts: " + " | ".join(sample))
        if ann.table_rows:
            sample_rows = ann.table_rows[:30]
            parts.append("table_rows:")
            for r in sample_rows:
                parts.append("  - " + "  ".join(r))
    return "\n".join(parts) if parts else "(空)"


def _build_prompt(
    sheet_ids: List[str],
    annotations: Dict[str, SheetAnnotations],
    rule_draft: RuleDraftDict,
    relevant_layers_by_sheet: Dict[str, List[str]],
) -> str:
    initial = _format_initial_draft(rule_draft)
    ctx = _format_sheet_context(annotations)
    layers_block = "\n".join(
        f"  - {sid}: {', '.join(relevant_layers_by_sheet.get(sid, [])) or '(无)'}"
        for sid in sheet_ids
    )

    fields_by_cat = {
        cat: [(fname, _FIELD_HUMAN.get(fname, fname), unit)
              for fname, _label, unit, _parser in CATEGORY_FIELDS[cat]]
        for cat in CATEGORIES
    }

    schema_lines: List[str] = []
    for cat, fields in fields_by_cat.items():
        schema_lines.append(f"  - {_CATEGORY_HUMAN[cat]} ({cat}):")
        for fname, label, unit in fields:
            schema_lines.append(f"    - {fname} ({label}{unit if unit else ''})")

    schema_text = "\n".join(schema_lines)

    return (
        "你是一名资深工程勘察/基坑设计图纸审核专家，需要在多张 CAD sheet 的「文字 + 表格」"
        "内容里交叉推理并给出最终工程量字段值。\n\n"
        "# 上下文\n\n"
        f"## 涉及 sheet 清单（VLM 已选中）\n{layers_block}\n\n"
        f"## 标注 / 表格聚合（已按 sheet 聚合）\n{ctx}\n\n"
        "## 阶段① 规则管线给出的初值（可被你覆盖）\n"
        f"{json.dumps(initial, ensure_ascii=False, indent=2)}\n\n"
        "# 任务\n\n"
        "请逐 category 给出最终 LLM 推理值。每个 category 一行（可能多张 sheet 合并为 1 行，"
        "也可能是多张 sheet 各自 1 行，请根据工程量语义自行合并）。\n\n"
        "输出严格 JSON 对象，**不要**任何解释 / Markdown / ```json fences：\n"
        "{\n"
        "  \"byCategory\": {\n"
        "    \"retaining_pile\": [\n"
        "      {\"id\": \"<sheet_id:category>\", \"sheetIds\": [\"<sheet_id>\", ...], \"fields\": {\n"
        "        \"<field_name>\": {\"value\": <数值或字符串或null>, \"trace\": \"<source|reasoning 简述>\"}\n"
        "      }},\n"
        "      ...\n"
        "    ],\n"
        "    \"bored_pile\": [...],\n"
        "    \"diaphragm_wall\": [...],\n"
        "    \"cap\": [...]\n"
        "  }\n"
        "}\n\n"
        "# 字段说明（按 category）：\n"
        f"{schema_text}\n\n"
        "# 重要规则\n"
        "1) 数值字段（count、diameter、area、volume、panel、length、width、depth、thickness）"
        "请尽量给出数值；如确实无法判断，给 null。\n"
        "2) trace 字段用「source|reasoning」格式，source 取 "
        "annotation / table / geometry / llm；reasoning 用一句话中文说明推理依据。\n"
        "3) 只填你已经能确认的字段；不要为了填满而瞎猜。\n"
        "4) 围护桩 / 钻孔灌注桩的 diameter 若同时存在 800/1000 等冲突，优先取「工程数量表」中"
        "明确给出的值，并在 trace 里写明冲突已解决。\n"
        "5) 连续墙组号（group）必须能在标注/表格里找到「第X组/第X段」字样，否则给 null。\n"
        "6) 承台编号（code）必须形如「CT-3」「CT-12」才能填入；其他写法给 null。\n"
        "7) sheetIds 列表写明该 row 由哪些 sheet 合并/确认得到，便于三方对账追溯。\n"
    )


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def _fallback_from_rule_draft(rule_draft: RuleDraftDict) -> Dict[str, Any]:
    """LLM 失败时把规则管线初值作为兜底。"""
    out: Dict[str, Any] = {"byCategory": {}}
    for cat in CATEGORIES:
        rows = rule_draft.byCategory.get(cat, [])
        if not rows:
            continue
        out_rows: List[Dict[str, Any]] = []
        for row in rows:
            fields: Dict[str, Dict[str, Any]] = {}
            for fname, info in row.fields.items():
                v = info.get("value")
                src = info.get("source") or "rule"
                trace = f"LLM 失败，沿用规则管线（{src}）"
                fields[fname] = {"value": v, "trace": trace[:160]}
            out_rows.append({"id": row.id, "sheetIds": [row.id.split(":")[0]], "fields": fields})
        out["byCategory"][cat] = out_rows
    return out


def _normalize_row(
    cat: str,
    item: Dict[str, Any],
    sheet_ids: List[str],
    fallback: Optional[RuleDraftRow] = None,
) -> Dict[str, Any]:
    """归一化单行 LlmResultRow；缺值用兜底。"""
    rid = str(item.get("id") or "").strip() or (fallback.id if fallback else "")
    if not rid:
        rid = f"{sheet_ids[0]}:{cat}" if sheet_ids else f":{cat}"

    raw_sheets = item.get("sheetIds") or item.get("sheet_ids") or []
    if isinstance(raw_sheets, str):
        raw_sheets = [raw_sheets]
    if not isinstance(raw_sheets, list):
        raw_sheets = []
    sids: List[str] = [str(s) for s in raw_sheets if str(s)]
    if not sids and sheet_ids:
        sids = [sheet_ids[0]]
    if not sids:
        sids = [rid.split(":")[0]] if ":" in rid else ["unknown"]

    fields_in = item.get("fields") or {}
    if not isinstance(fields_in, dict):
        fields_in = {}

    # 兜底字段映射（RuleDraftRow.fields: {fname: {value, source}}）
    fallback_field_map: Dict[str, Dict[str, Any]] = {}
    if fallback is not None:
        for fname, info in (fallback.fields or {}).items():
            if isinstance(info, dict):
                fallback_field_map[fname] = info

    fields_out: Dict[str, Dict[str, Any]] = {}
    field_specs = CATEGORY_FIELDS.get(cat, [])
    for fname, _label, _unit, _parser in field_specs:
        f = fields_in.get(fname) or {}
        if not isinstance(f, dict):
            f = {}
        value = f.get("value", None)
        trace = str(f.get("trace") or f.get("source") or "").strip()
        fb_info = fallback_field_map.get(fname) or {}
        fb_value = fb_info.get("value")
        fb_source = fb_info.get("source") or "rule"

        # 数值字段统一为数字或 null；LLM 没填则用规则兜底值
        if fname in (
            "count", "diameter", "area", "volume", "panel",
            "length", "width", "depth", "thickness",
        ):
            if value is None or value == "":
                value = fb_value
            else:
                try:
                    value = float(value)
                except Exception:
                    try:
                        value = int(value)
                        value = float(value)
                    except Exception:
                        value = fb_value
        # 字符串字段（type/shape/code/group）：LLM 没填就用兜底
        if value is None and fname in ("type", "shape", "code", "group"):
            value = fb_value

        if not trace:
            if fb_value not in (None, "") and value == fb_value:
                trace = f"沿用规则管线（{fb_source}）"
            else:
                trace = "llm 自动推理"
        trace = trace[:160]
        fields_out[fname] = {"value": value, "trace": trace}
    return {"id": rid, "sheetIds": sids, "fields": fields_out}


async def run_llm_infer(
    provider: Provider,
    sheet_dir: str,
    sheet_ids: List[str],
    rule_draft: RuleDraftDict,
    relevant_layers_by_sheet: Optional[Dict[str, List[str]]] = None,
    *,
    on_progress: Optional[callable] = None,
) -> Dict[str, Any]:
    """在 VLM 选中的 sheet 上做文本 LLM 推理。

    :param provider: 文本 Provider（OpenAI 兼容 / Anthropic / Gemini）
    :param sheet_dir: sheet 目录
    :param sheet_ids: VLM 选中且用户二次确认的 sheet 列表
    :param rule_draft: 阶段① 规则管线产物
    :param relevant_layers_by_sheet: VLM 给出的图层提示 → 后续可扩展做按图层过滤
    :param on_progress: 可选回调
    :returns: LlmResult dict {byCategory: {cat: [LlmResultRow, ...]}}
    """
    relevant_layers_by_sheet = relevant_layers_by_sheet or {}

    if not sheet_ids:
        return {"byCategory": {c: [] for c in CATEGORIES}}

    # 1) 抽取选中 sheet 的标注聚合
    annotations: Dict[str, SheetAnnotations] = {}
    total = len(sheet_ids)
    for i, sid in enumerate(sheet_ids):
        ann = _load_sheet_annotations(sheet_dir, sid)
        ann.sheet_name = ann.sheet_name or sid
        annotations[sid] = ann
        if on_progress:
            try:
                on_progress(i + 1, total, f"已抽取 sheet {sid} 标注")
            except Exception:
                pass

    # 2) prompt
    prompt = _build_prompt(sheet_ids, annotations, rule_draft, relevant_layers_by_sheet)
    if on_progress:
        try:
            on_progress(total, total, "已构造 prompt，送文本 LLM")
        except Exception:
            pass

    # 3) chat
    try:
        messages = [{"role": "user", "content": prompt}]
        raw = await provider.chat(messages, temperature=0.2)
    except Exception as exc:
        msg = getattr(exc, "message", None) or str(exc)
        log.warning("llm_infer: chat failed: %s", msg)
        if on_progress:
            try:
                on_progress(total, total, f"LLM 失败：{msg[:60]}")
            except Exception:
                pass
        return _fallback_from_rule_draft(rule_draft)

    # 4) JSON 解析
    try:
        parsed = _robust_json_loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("模型输出不是 JSON 对象")
        by_cat_raw = parsed.get("byCategory") or parsed.get("by_category") or {}
        if not isinstance(by_cat_raw, dict):
            raise ValueError("byCategory 不是对象")
    except Exception as exc:
        log.warning("llm_infer: parse failed: %s; raw=%s", exc, (raw or "")[:300])
        if on_progress:
            try:
                on_progress(total, total, f"LLM 输出解析失败，沿用规则管线")
            except Exception:
                pass
        return _fallback_from_rule_draft(rule_draft)

    # 5) 归一化
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for cat in CATEGORIES:
        items = by_cat_raw.get(cat) or []
        if not isinstance(items, list):
            items = []
        # 兜底映射：若 LLM 完全没填该 category，逐条用 rule_draft 生成
        fallback_rows: List[RuleDraftRow] = list(rule_draft.byCategory.get(cat, []))
        norm_rows: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            rid = str(it.get("id") or "")
            fb = next((r for r in fallback_rows if r.id == rid), None)
            norm_rows.append(_normalize_row(cat, it, sheet_ids, fallback=fb))
        # 若 LLM 整体漏掉该 category / 该 category 一行都没出，逐条用规则初值兜底
        if not norm_rows:
            for r in fallback_rows:
                norm_rows.append(_normalize_row(
                    cat, {"id": r.id, "sheetIds": [r.id.split(":")[0]], "fields": {}}, sheet_ids, fallback=r,
                ))
        by_category[cat] = norm_rows

    if on_progress:
        try:
            on_progress(total, total, "LLM 推理完成")
        except Exception:
            pass

    return {"byCategory": by_category}


__all__ = ["run_llm_infer"]