# -*- coding: utf-8 -*-
"""阶段② VLM 筛选：让视觉大模型从一组 sheet PNG 中挑出与目标工程量相关的图。

设计要点：
    1) 把每张 sheet PNG 缩放成统一缩略图，拼接成一张 contact sheet（拼图），
       单次 vision 调用覆盖所有 sheet，节省成本与延迟；
    2) prompt 引导模型返回严格的 JSON 数组（每个 sheet 一个对象）：
         {sheetId, score:0~1, role, reason, relevantLayers:[...]}
       解析层对 ```json fences 与尾部逗号做容错；
    3) 与 rules.py 一致：VLM 在筛选时同时给出「该图涉及到的图层
       relevantLayers」，让下游规则管线（阶段①）能按图层过滤目标数据。
       这一层由 VLM 给出比纯文本/几何启发更可靠。
    4) 输出与前端 types.ts 的 VlmCandidate 完全一致。

依赖：
    - PIL（Pillow）做缩略图拼接
    - ezdxf + matplotlib 在 render.py 里已声明
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..llm.openai_compat import ProviderError
from ..llm.provider import Provider
from .render import render_dxf_to_png, render_sheet_to_cache


log = logging.getLogger(__name__)


# 缩略图单元格边长（拼接后大图的 cell 边长；最后整图再缩放到最大 2048 px）
_THUMB_CELL_PX = 256
_CONTACT_MAX_PX = 2048


# --------------------------------------------------------------------------
# 工具：拼 contact sheet
# --------------------------------------------------------------------------


def _try_import_pil():
    try:
        from PIL import Image  # type: ignore
        return Image
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "VLM 筛选需要 Pillow，请先 pip install Pillow"
        ) from exc


def build_contact_sheet(
    sheet_pngs: List[Tuple[str, bytes]],
    *,
    cell_px: int = _THUMB_CELL_PX,
    max_px: int = _CONTACT_MAX_PX,
) -> bytes:
    """把多张 PNG 缩成统一缩略图，网格拼接为一张大图。

    :param sheet_pngs: [(sheetId, png_bytes), ...]
    :param cell_px: 单格边长（px，正方形）
    :param max_px: 最终整图最长边上限
    """
    Image = _try_import_pil()

    if not sheet_pngs:
        raise ValueError("sheet_pngs 不能为空")

    n = len(sheet_pngs)
    cols = 1
    while cols * cols < n:
        cols += 1
    rows = (n + cols - 1) // cols

    canvas = Image.new("RGB", (cols * cell_px, rows * cell_px), color="white")
    for idx, (sheet_id, data) in enumerate(sheet_pngs):
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            im = Image.new("RGB", (cell_px, cell_px), color="lightgray")
        # 等比 fit
        im.thumbnail((cell_px, cell_px))
        # 把图像居中放到 cell
        cell = Image.new("RGB", (cell_px, cell_px), color="white")
        off_x = (cell_px - im.width) // 2
        off_y = (cell_px - im.height) // 2
        cell.paste(im, (off_x, off_y))
        # 在左上角写 sheetId（红色）
        try:
            from PIL import ImageDraw, ImageFont
            d = ImageDraw.Draw(cell)
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
            d.rectangle([(0, 0), (cell_px, 14)], fill=(255, 0, 0))
            d.text((2, 0), sheet_id[:18], fill="white", font=font)
        except Exception:
            pass
        r, c = divmod(idx, cols)
        canvas.paste(cell, (c * cell_px, r * cell_px))

    # 等比缩放到 max_px
    if max(canvas.size) > max_px:
        canvas.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# JSON 容错解析
# --------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _robust_json_loads(text: str) -> Any:
    """从模型输出里抽 JSON：处理 ```json fences / 前置后置文本。"""
    text = (text or "").strip()
    # 1) 抽取代码块
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    # 2) 尝试找到首/尾 [ ]
    if "[" in text and "]" in text:
        l = text.find("[")
        r = text.rfind("]")
        if l != -1 and r != -1 and r > l:
            text = text[l : r + 1]
    # 3) 移除尾部逗号
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return json.loads(text)


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

_PROMPT_TEMPLATE = """你是一名资深的工程勘察/基坑设计图纸审核专家。

下面是一份 DWG 图纸被拆分成多张 sheet 后渲染出的「缩略拼接图」。每张缩略图左上角的红色标签是该 sheet 的 ID。

sheetIds 列表（顺序与拼图从左到右、从上到下一致）：
{ids}

请严格按以下 JSON 数组格式输出，**不要**输出其他任何文字、不要使用 ```json fences：
[
  {{
    "sheetId": "<sheet id>",
    "score": <0.0~1.0>,
    "role": "<plan | section | detail | legend | title | schedule | other>",
    "reason": "<≤30 字中文，说明为什么选/不选>",
    "relevantLayers": ["<图层名>", ...]
  }},
  ...
]

选图原则（用于「基坑围护」工程量复核，请重点关注）：
1) 围护桩/钻孔灌注桩的「平面布置图」「剖面图」以及对应的「工程数量表」score 0.7~1.0
2) 地下连续墙的「平面图」「剖面图」「配筋图」以及「幅数/长度/深度」相关说明，score 0.7~1.0
3) 承台「平面图」「详图」+「编号/形状/厚度」相关，score 0.7~1.0
4) 图纸目录、设计总说明 score 0.0~0.2；标题栏 score 0.0~0.05
5) 对每张选中的图（score ≥ 0.4），列出图上**实际涉及到的图层名**（图框/标题栏图层不计入）；若无法判断请返回空数组

只返回 JSON 数组，不要任何解释、说明、Markdown。"""


def _build_prompt(sheet_ids: List[str]) -> str:
    return _PROMPT_TEMPLATE.format(ids=json.dumps(sheet_ids, ensure_ascii=False))


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


async def run_vlm_selection(
    provider: Provider,
    sheet_dir: str,
    sheet_ids: List[str],
    *,
    on_progress: Optional[callable] = None,
) -> List[Dict[str, Any]]:
    """对 sheet_dir 下指定 sheet 跑 VLM 筛选。

    :param provider: 配置 provider 对象（要求 vision_model 已设置）
    :param sheet_ids: 待筛选 sheet id 列表（顺序与拼图一致）
    :param on_progress: 可选回调 (i, total, msg)
    :returns: VlmCandidate 列表，每个含 sheetId / score / role / reason / relevantLayers
    """
    if not sheet_ids:
        return []

    # 1) 渲染每张 sheet（命中缓存）→ 缩略图 PNG bytes
    sheet_pngs: List[Tuple[str, bytes]] = []
    total = len(sheet_ids)
    for i, sid in enumerate(sheet_ids):
        png_path = render_sheet_to_cache(sheet_dir, sid)
        if png_path is None:
            log.warning("vlm_select: render failed for %s, fallback to empty", sid)
            # 仍占位一张 1x1 透明 PNG，保证拼图按原 sheetIds 顺序
            data = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            )
        else:
            with open(png_path, "rb") as fp:
                data = fp.read()
        sheet_pngs.append((sid, data))
        if on_progress:
            try:
                on_progress(i + 1, total, f"渲染 {sid}")
            except Exception:
                pass

    # 2) 拼 contact sheet
    contact = build_contact_sheet(sheet_pngs)
    if on_progress:
        try:
            on_progress(total, total, "已拼接 contact sheet，送 VLM")
        except Exception:
            pass

    # 3) VLM 调用（vision）
    prompt = _build_prompt(sheet_ids)
    try:
        raw = await provider.vision([contact], prompt)
    except Exception as exc:
        # 既要抓 ProviderError，也要抓任意 RuntimeError 等（mock / 自定义实现可能不抛 ProviderError）
        msg = getattr(exc, "message", None) or str(exc)
        log.warning("vlm_select: vision call failed: %s", msg)
        return [
            {
                "sheetId": sid,
                "score": 0.0,
                "role": "other",
                "reason": f"VLM 失败：{msg[:60]}",
                "relevantLayers": [],
            }
            for sid in sheet_ids
        ]

    # 4) JSON 解析
    try:
        parsed = _robust_json_loads(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("candidates") or parsed.get("data") or parsed.get("items") or []
        if not isinstance(parsed, list):
            raise ValueError("模型返回的不是 JSON 数组")
    except Exception as exc:
        log.warning("vlm_select: parse failed: %s; raw=%s", exc, (raw or "")[:300])
        return [
            {
                "sheetId": sid,
                "score": 0.0,
                "role": "other",
                "reason": f"VLM 解析失败：{str(exc)[:60]}",
                "relevantLayers": [],
            }
            for sid in sheet_ids
        ]

    # 5) 归一化为 VlmCandidate
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("sheetId") or item.get("sheet_id") or "").strip()
        if not sid or sid not in sheet_ids:
            continue
        try:
            score = float(item.get("score", 0.0))
        except Exception:
            score = 0.0
        score = max(0.0, min(1.0, score))
        role_val = str(item.get("role") or "other").strip().lower() or "other"
        reason_val = str(item.get("reason") or "").strip()[:200]
        layers = item.get("relevantLayers") or item.get("layers") or []
        if not isinstance(layers, list):
            layers = []
        layers = [str(l).strip() for l in layers if str(l).strip()]
        by_id[sid] = {
            "sheetId": sid,
            "score": round(score, 3),
            "role": role_val,
            "reason": reason_val,
            "relevantLayers": layers,
        }

    # 6) 缺失 sheet 补占位（极少见，模型漏评）
    out: List[Dict[str, Any]] = []
    for sid in sheet_ids:
        if sid in by_id:
            out.append(by_id[sid])
        else:
            out.append({
                "sheetId": sid,
                "score": 0.0,
                "role": "other",
                "reason": "未在模型输出中找到（可能漏评）",
                "relevantLayers": [],
            })

    if on_progress:
        try:
            on_progress(total, total, f"VLM 筛选完成 {len(out)} 张")
        except Exception:
            pass
    return out


__all__ = [
    "build_contact_sheet",
    "run_vlm_selection",
]