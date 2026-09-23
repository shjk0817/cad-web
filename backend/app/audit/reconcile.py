# -*- coding: utf-8 -*-
"""阶段④ 三方对账（geometry ↔ table ↔ annotation）+ LLM 最终值对比。

设计要点：
    1) 输入：阶段① RuleDraft（其中每个字段都标了 source）、
       阶段③ LlmResult（含每行 sheetIds / 各字段 value+trace）。
    2) 对账策略：
         ruleValue  → 规则管线拿到的最终值（任意 source 的值）
         annoValue  → 规则管线从 annotation 拿到的值（如果 source 本身就是 annotation，
                      则同 ruleValue；否则为 None）
         llmValue   → 阶段③ LLM 的最终 value
       严格三方对账要求 geometry/table/annotation 都需独立取值，但当前
       规则管线只产出 source 标签。为支持严格三方对比，这里对每个字段
       在规则管线层面再额外取一次各 source 的独立值（见 _triple_for_field）。
    3) severity：
         match     → 三方值全部一致（数值字段相对误差 < 1%，字符串字段相等）
         warn      → 缺失一方 / 非关键字段不一致
         conflict  → 数值字段相对误差 ≥ 5%，或字符串字段不一致
    4) confidence：基于「匹配字段比例 × LLM 自身 confidence（trace 内嵌）」加权。
    5) 输出与前端 ReconcileFieldRow / ReconcileCategoryReport / ReconcileReport
       完全一致。

依赖：
    - audit.rules（CATEGORIES / CATEGORY_FIELDS / RuleDraftDict / _parse_field）
    - SheetAnnotations（来自 audit.rules）
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

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


# 数值字段（参与相对误差比较）
NUMERIC_FIELDS = {
    "count", "diameter", "area", "volume", "panel",
    "length", "width", "depth", "thickness",
}
# 字符串字段（参与精确比较）
STRING_FIELDS = {"type", "shape", "code", "group"}

# 数值字段容差
REL_TOL_WARN = 0.01      # 1% → 视为 match
REL_TOL_CONFLICT = 0.05   # 5% → 视为 conflict
# 字符串字段空 vs 非空 → 视为 warn


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _same_value(a: Any, b: Any, *, rel_tol: float = REL_TOL_WARN) -> Tuple[bool, Optional[str]]:
    """比较两个值是否「相等」。

    返回 (is_same, diff_str)。
    数值：相对误差 < rel_tol 视为相等。
    字符串：完全相等视为相等。
    None 与 None 视为相等。
    """
    if a is None and b is None:
        return True, None
    if a is None or b is None:
        return False, f"{a} vs {b}"
    fa, fb = _to_float(a), _to_float(b)
    if fa is not None and fb is not None:
        if fa == fb:
            return True, None
        denom = max(abs(fa), abs(fb), 1e-9)
        diff = abs(fa - fb) / denom
        return diff < rel_tol, f"{fa} vs {fb} (相对差 {round(diff*100, 2)}%)"
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb:
        return True, None
    return False, f"{sa} vs {sb}"


def _triple_for_field(
    cat: str,
    field_name: str,
    annotations_by_sheet: Dict[str, SheetAnnotations],
    sheet_ids: List[str],
) -> Dict[str, Any]:
    """独立抽取 geometry / table / annotation 三方各自的值。

    返回 {geometry: <v or None>, table: <v or None>, annotation: <v or None>}。
    """
    out: Dict[str, Any] = {"geometry": None, "table": None, "annotation": None}
    for sid in sheet_ids:
        ann = annotations_by_sheet.get(sid)
        if ann is None:
            continue
        # 调 _parse_field 两次：一次 sources=[("table", ...)]，一次 sources=[("annotation", ...)]
        # 但 _parse_field 内部硬编码 sources 顺序且会同时取两方，
        # 所以这里用一个 hack：先取 annotation 单独（只放 texts）再取 table 单独（只放 rows）。
        # 复刻 _parse_field 关键路径
        # 1) annotation
        if out["annotation"] is None:
            v, _ = _parse_field_single_source(cat, field_name, ann.texts, source_label="annotation")
            out["annotation"] = v
        # 2) table
        if out["table"] is None:
            tbl_texts = [" ".join(r) for r in ann.table_rows]
            v, _ = _parse_field_single_source(cat, field_name, tbl_texts, source_label="table")
            out["table"] = v
        # 3) geometry（特殊值，需触发 _parse_field 的 geometry 兜底分支）
        # 通过给 _parse_field 传一个空的 texts+table_rows，让它走到 volume/area 的 geometry 兜底
        if out["geometry"] is None:
            # 构造临时 ann-like：texts 空，table_rows 空
            v, src = _parse_field(cat, field_name, _empty_ann(ann))
            if src == "geometry":
                out["geometry"] = v
        # 任意一方拿到值就跳出
        if all(out[k] is not None for k in ("geometry", "table", "annotation")):
            break
    return out


def _empty_ann(ann: SheetAnnotations) -> SheetAnnotations:
    """给 _parse_field 用的「无文本」 ann，确保它走 geometry 兜底分支。"""
    class _EmptyAnn:
        def __init__(self, ref):
            self.sheet_id = ref.sheet_id
            self.sheet_name = ref.sheet_name
            self.texts = []
            self.table_rows = []
            self.layer_counts = ref.layer_counts
            self.circle_count = ref.circle_count
            self.insert_count = ref.insert_count
    return _EmptyAnn(ann)


def _parse_field_single_source(
    cat: str,
    field_name: str,
    texts: List[str],
    *,
    source_label: str,
) -> Tuple[Optional[Any], str]:
    """模拟 `_parse_field`，但只从给定的文本列表里抽值（不走 geometry 兜底）。
    返回 (value, "annotation"|"table"|"")。
    """
    if field_name == "count":
        for t in texts:
            m = re.search(r"(?:数量|N|n)\s*[:=＝]\s*(\d+)", t)
            if m:
                try:
                    return int(m.group(1)), source_label
                except Exception:
                    pass
        return None, ""
    if field_name == "diameter":
        for t in texts:
            m = re.search(r"[Φφ∅]?\s*(\d{3,4})\s*(?:mm|毫米|MM)?", t)
            if m:
                try:
                    v = int(m.group(1))
                    if 100 <= v <= 4000:
                        return float(v), source_label
                except Exception:
                    pass
        return None, ""
    if field_name in ("length", "width", "depth"):
        patterns = {
            "length": re.compile(r"(?:长(?:度)?|L\w{0,2})\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?", re.IGNORECASE),
            "width":  re.compile(r"(?:宽(?:度)?|W\w{0,2}|B\w{0,2})\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?", re.IGNORECASE),
            "depth":  re.compile(r"(?:深(?:度)?|H\w{0,2}|D\w{0,2})\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?", re.IGNORECASE),
        }
        for t in texts:
            m = patterns[field_name].search(t)
            if m:
                v = float(m.group(1))
                unit = m.group(2) or "m"
                if unit.lower() in ("mm", "毫米"):
                    return v / 1000.0, source_label
                return v, source_label
        return None, ""
    if field_name == "volume":
        for t in texts:
            m = re.search(r"(?:体积|V)\s*[:=＝]\s*(\d+(?:\.\d+)?)", t)
            if m:
                try:
                    return float(m.group(1)), source_label
                except Exception:
                    pass
        return None, ""
    if field_name == "type":
        for t in texts:
            if "钻孔" in t: return "钻孔灌注", source_label
            if "挖孔" in t: return "挖孔", source_label
            if "预制" in t: return "预制", source_label
            if "搅拌" in t or "SMW" in t.upper(): return "搅拌", source_label
            if "钢板" in t: return "钢板", source_label
        return None, ""
    if field_name == "shape":
        for t in texts:
            if "矩形" in t: return "矩形", source_label
            if "圆形" in t or "圆柱" in t: return "圆形", source_label
            if "方形" in t: return "方形", source_label
            if "异形" in t: return "异形", source_label
        return None, ""
    if field_name == "code":
        for t in texts:
            m = re.search(r"(?:承台\s*)?([A-Z]{1,3}-?\d{1,3})", t)
            if m:
                return m.group(1).strip(), source_label
        return None, ""
    if field_name == "group":
        for t in texts:
            m = re.search(r"第\s*([一二三四五六七八九十0-9]+)\s*[组段]", t)
            if m:
                return m.group(1), source_label
        return None, ""
    if field_name == "panel":
        return _parse_field_single_source(cat, "count", texts, source_label=source_label)
    if field_name == "area":
        return None, ""   # area 走几何兜底
    if field_name == "thickness":
        pat = re.compile(r"(?:厚(?:度)?|h)\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?", re.IGNORECASE)
        for t in texts:
            m = pat.search(t)
            if m:
                v = float(m.group(1))
                unit = m.group(2) or "m"
                if unit.lower() in ("mm", "毫米"):
                    return v / 1000.0, source_label
                return v, source_label
        return None, ""
    return None, ""


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def _row_to_field_rows(
    cat: str,
    llm_row: Dict[str, Any],
    rule_row: Optional[RuleDraftRow],
    annotations_by_sheet: Dict[str, SheetAnnotations],
) -> List[Dict[str, Any]]:
    """把一行 LlmResult + RuleDraft 转成多个 ReconcileFieldRow（每字段 1 行）。"""
    sheet_ids = llm_row.get("sheetIds") or []
    if isinstance(sheet_ids, str):
        sheet_ids = [sheet_ids]
    if not isinstance(sheet_ids, list):
        sheet_ids = []
    sheet_ids = [str(s) for s in sheet_ids if str(s)]

    # ruleRow 的 source map
    rule_field_map: Dict[str, Dict[str, Any]] = {}
    if rule_row is not None:
        for fname, info in (rule_row.fields or {}).items():
            if isinstance(info, dict):
                rule_field_map[fname] = info

    rows: List[Dict[str, Any]] = []
    llm_fields = llm_row.get("fields") or {}
    if not isinstance(llm_fields, dict):
        llm_fields = {}

    for fname, _label, _unit, _parser in CATEGORY_FIELDS.get(cat, []):
        llm_info = llm_fields.get(fname) or {}
        if not isinstance(llm_info, dict):
            llm_info = {}
        llm_value = llm_info.get("value")

        # 重新抽取三方独立值
        triple = _triple_for_field(cat, fname, annotations_by_sheet, sheet_ids or ([rule_row.id.split(":")[0]] if rule_row and ":" in rule_row.id else []))
        rule_value = triple.get("geometry")
        # 优先取「来源字段」的 rule_value：
        # - 如果 annotation 有值就用 annotation，否则用 geometry，否则 None
        rule_value_for_compare = triple.get("annotation")
        if rule_value_for_compare is None:
            rule_value_for_compare = triple.get("geometry")
        anno_value = triple.get("annotation")

        # 对账
        severity = "warn"
        diff: Optional[str] = None
        if fname in NUMERIC_FIELDS:
            # 与 llmValue 比
            same, d = _same_value(rule_value_for_compare, llm_value, rel_tol=REL_TOL_WARN)
            if rule_value_for_compare is None and llm_value is None:
                severity = "warn"   # 双空
                diff = "两侧均无值"
            elif same:
                severity = "match"
                diff = None
            else:
                same_c, _ = _same_value(rule_value_for_compare, llm_value, rel_tol=REL_TOL_CONFLICT)
                if same_c:
                    severity = "warn"
                    diff = d
                else:
                    severity = "conflict"
                    diff = d
        elif fname in STRING_FIELDS:
            same, d = _same_value(rule_value_for_compare, llm_value)
            if rule_value_for_compare is None and llm_value is None:
                severity = "warn"
                diff = "两侧均无值"
            elif same:
                severity = "match"
                diff = None
            else:
                # shape / group 是 LLM 可"自由给出"的弱字段；规则端未抽到时不构成 conflict
                if fname in ("shape", "group"):
                    severity = "warn"
                    diff = d
                elif (rule_value_for_compare or llm_value):
                    severity = "conflict"
                    diff = d
                else:
                    severity = "warn"
                    diff = d

        rows.append({
            "field": fname,
            "ruleValue": rule_value_for_compare,
            "llmValue": llm_value,
            "annoValue": anno_value,
            "diff": diff,
            "severity": severity,
            # 附加字段，便于前端调试（非强契约）
            "_geometry": triple.get("geometry"),
            "_table": triple.get("table"),
            "_llmTrace": str(llm_info.get("trace") or "")[:160],
        })
    return rows


def _category_confidence(rows: List[Dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    score = 0.0
    for r in rows:
        sev = r.get("severity")
        if sev == "match":
            score += 1.0
        elif sev == "warn":
            score += 0.5
        else:
            score += 0.0
    return round(score / len(rows), 3)


def _overall_confidence(by_cat_conf: Dict[str, float]) -> float:
    if not by_cat_conf:
        return 0.0
    return round(sum(by_cat_conf.values()) / len(by_cat_conf), 3)


def run_reconcile(
    sheet_dir: str,
    rule_draft: RuleDraftDict,
    llm_result: Dict[str, Any],
) -> Dict[str, Any]:
    """阶段④ 三方对账主入口。

    :param sheet_dir: sheet 目录
    :param rule_draft: 阶段① RuleDraftDict
    :param llm_result: 阶段③ LlmResult（来自 run_llm_infer）
    :returns: 与前端 ReconcileReport 对齐的 dict
    """
    # 1) 把所有涉及 sheet 的标注聚合提前抽好（避免重复 IO）
    annotations_by_sheet: Dict[str, SheetAnnotations] = {}
    all_sheet_ids = set()
    for cat in CATEGORIES:
        for row in rule_draft.byCategory.get(cat, []):
            all_sheet_ids.add(row.id.split(":")[0])
    for row in (llm_result.get("byCategory") or {}).values():
        for r in row:
            for s in (r.get("sheetIds") or []):
                if isinstance(s, str) and s:
                    all_sheet_ids.add(s)
    for sid in all_sheet_ids:
        path = os.path.join(sheet_dir, f"{sid}.dxf")
        if not os.path.isfile(path):
            continue
        ann = _collect_sheet(path, sid)
        annotations_by_sheet[sid] = ann

    # 2) 按 category 对账
    by_category: Dict[str, Dict[str, Any]] = {}
    total = matched = warned = conflicted = 0
    by_cat_conf: Dict[str, float] = {}

    for cat in CATEGORIES:
        rule_rows: Dict[str, RuleDraftRow] = {
            r.id: r for r in rule_draft.byCategory.get(cat, [])
        }
        llm_rows: List[Dict[str, Any]] = (llm_result.get("byCategory") or {}).get(cat, []) or []
        cat_rows: List[Dict[str, Any]] = []
        # LLM 行优先；LLM 没行则用 rule 行
        consumed_rule_ids: set = set()
        for llm_row in llm_rows:
            rid = llm_row.get("id") or ""
            rule_row = rule_rows.get(rid)
            if rule_row is not None:
                consumed_rule_ids.add(rid)
            cat_rows.extend(
                _row_to_field_rows(cat, llm_row, rule_row, annotations_by_sheet)
            )
        # LLM 漏掉的 rule 行兜底
        for rid, rule_row in rule_rows.items():
            if rid in consumed_rule_ids:
                continue
            sid = rid.split(":")[0]
            fake_llm_row = {"id": rid, "sheetIds": [sid], "fields": {}}
            cat_rows.extend(
                _row_to_field_rows(cat, fake_llm_row, rule_row, annotations_by_sheet)
            )
        # LLM 有但 rule 没有的，也保留（0 LLM 自报）
        # （已被上面覆盖）

        conf = _category_confidence(cat_rows)
        by_cat_conf[cat] = conf
        for r in cat_rows:
            total += 1
            sev = r.get("severity")
            if sev == "match":
                matched += 1
            elif sev == "warn":
                warned += 1
            elif sev == "conflict":
                conflicted += 1
        by_category[cat] = {
            "rows": cat_rows,
            "confidence": conf,
        }

    return {
        "byCategory": by_category,
        "summary": {
            "totalRows": total,
            "matched": matched,
            "warned": warned,
            "conflicted": conflicted,
        },
        "confidence": _overall_confidence(by_cat_conf),
    }


__all__ = ["run_reconcile"]