# -*- coding: utf-8 -*-
"""阶段① 规则管线：纯几何 + 标注/表格聚合推断。

输出结构与前端 types.ts 中的 RuleDraft 完全一致：

    {
      "byCategory": {
        "retaining_pile":  [RuleDraftRow, ...],
        "bored_pile":      [RuleDraftRow, ...],
        "diaphragm_wall":  [RuleDraftRow, ...],
        "cap":             [RuleDraftRow, ...],
      },
      "summary": { ..., "bySheet": [{sheetId, hits: {...}}, ...] }
    }

每个 RuleDraftRow.fields 的 value 都附带 source：
    - "annotation"   从图纸上的标注文字/表格直接命中
    - "geometry"     由几何启发式推断（如成簇圆统计）
    - "table"        来自表格聚合（编号 / 数量 / 桩径 / 长度 / 体积…）

字段约定（与 spec.md 对齐）：
    围护桩 (retaining_pile):    type / count / diameter / area / volume
    钻孔灌注桩 (bored_pile):    count / diameter / area / volume
    地下连续墙 (diaphragm_wall): group / panel / length / width / depth / volume
    承台 (cap):                 code / count / shape / area / thickness / volume

注意：本管线是「启发式初值」，不是终值；阶段④三方对账会用 geometry ↔
table ↔ annotation 再次核对，阶段③文本 LLM 会给出推理值。
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import ezdxf
from ezdxf import bbox


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 分类与字段定义
# --------------------------------------------------------------------------

CATEGORIES: Tuple[str, ...] = (
    "retaining_pile",
    "bored_pile",
    "diaphragm_wall",
    "cap",
)


# 各类字段定义：(name, label, unit, parser_key)
# parser_key 在 _PARSERS 中查找具体解析函数；空表示该字段只走人工/几何兜底。
CATEGORY_FIELDS: Dict[str, List[Tuple[str, str, str, str]]] = {
    "retaining_pile": [
        ("type", "桩型", "", "type"),
        ("count", "数量", "根", "count"),
        ("diameter", "桩径", "mm", "diameter"),
        ("area", "单根截面积", "m²", "area"),
        ("volume", "体积", "m³", "volume"),
    ],
    "bored_pile": [
        ("count", "数量", "根", "count"),
        ("diameter", "桩径", "mm", "diameter"),
        ("area", "单根截面积", "m²", "area"),
        ("volume", "体积", "m³", "volume"),
    ],
    "diaphragm_wall": [
        ("group", "组号", "", "group"),
        ("panel", "幅数", "幅", "count"),
        ("length", "长度", "m", "length"),
        ("width", "宽度", "m", "width"),
        ("depth", "深度", "m", "depth"),
        ("volume", "体积", "m³", "volume"),
    ],
    "cap": [
        ("code", "承台编号", "", "code"),
        ("count", "数量", "个", "count"),
        ("shape", "形状", "", "shape"),
        ("area", "面积", "m²", "area"),
        ("thickness", "厚度", "m", "thickness"),
        ("volume", "体积", "m³", "volume"),
    ],
}


# 分类关键字：用于 sheet 级别的命中。优先级从高到低。
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "diaphragm_wall":  ["地连墙", "连续墙", "地下连续墙", "DIAPHRAGM"],
    "retaining_pile":  ["围护桩", "排桩", "RETAINING"],
    "bored_pile":      ["钻孔灌注桩", "灌注桩", "钻孔桩", "BORED"],
    "cap":             ["承台", "CAP"],
}


# 图层名 → 类别弱线索（兜底）
LAYER_HINTS: Dict[str, str] = {
    "围护桩":  "retaining_pile",
    "排桩":    "retaining_pile",
    "钻孔桩":  "bored_pile",
    "灌注桩":  "bored_pile",
    "连续墙":  "diaphragm_wall",
    "地连墙":  "diaphragm_wall",
    "承台":    "cap",
}


# --------------------------------------------------------------------------
# 正则模式
# --------------------------------------------------------------------------

RE_DIAMETER = re.compile(r"[Φφ∅]?\s*(\d{3,4})\s*(?:mm|毫米|MM)?")
RE_NUMBER = re.compile(r"(?<![A-Za-z\d])(\d{1,5})(?:\s*[根幅个])?")
RE_LENGTH = re.compile(r"(?:长(?:度)?|L\w{0,2})\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?", re.IGNORECASE)
RE_WIDTH = re.compile(r"(?:宽(?:度)?|W\w{0,2}|B\w{0,2})\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?", re.IGNORECASE)
RE_DEPTH = re.compile(r"(?:深(?:度)?|H\w{0,2}|D\w{0,2})\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?", re.IGNORECASE)
RE_COUNT_KV = re.compile(r"(?:数量|N|n)\s*[:=＝]\s*(\d+)")
RE_VOLUME_KV = re.compile(r"(?:体积|V)\s*[:=＝]\s*(\d+(?:\.\d+)?)")
RE_CAP_CODE = re.compile(r"(?:承台\s*)?([A-Z]{1,3}-?\d{1,3})")


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class SheetAnnotations:
    """一张 sheet 上抽出的所有文字 / 表格行 / 图层 / 几何统计。"""

    sheet_id: str
    sheet_name: str = ""
    texts: List[str] = field(default_factory=list)            # 全部文字内容
    table_rows: List[List[str]] = field(default_factory=list) # 简化表格行（按行聚合）
    layer_counts: Dict[str, int] = field(default_factory=dict)
    circle_count: int = 0        # CIRCLE 实体数（圆形桩/桩位近似）
    insert_count: int = 0        # INSERT 实体数（块参照）


@dataclass
class RuleDraftRow:
    id: str
    fields: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class RuleDraftDict:
    byCategory: Dict[str, List[RuleDraftRow]]
    summary: Dict[str, Any]


# --------------------------------------------------------------------------
# 抽取层
# --------------------------------------------------------------------------

def _entity_plain_text(entity) -> str:
    if entity.dxftype() == "MTEXT":
        try:
            return entity.plain_text()
        except Exception:
            try:
                return entity.text
            except Exception:
                return ""
    try:
        return entity.dxf.text or ""
    except Exception:
        return ""


def _approx_table_rows(layout) -> List[List[str]]:
    """把图纸空间里 Y 坐标接近的文字聚合为「表格行」近似。

    CAD 图纸上的「工程数量表」通常是若干行 TEXT/MTEXT 按行排列、列对齐
    到相同 X 坐标。这里采用容差聚类（同一 Y 值 ±0.5 * 平均字高视为同一行），
    再按 X 排序得到每行的字段序列。属于粗略启发式，便于阶段④做三方对账。
    """
    rows: List[List[Tuple[float, float, str]]] = []
    bucket: List[Tuple[float, float, str]] = []
    # 仅取文字实体
    text_entities = [e for e in layout if e.dxftype() in ("TEXT", "MTEXT")]
    if not text_entities:
        return []
    # 先把所有文字放到 bucket 并记 y 中心 + x 起点
    items: List[Tuple[float, float, str]] = []
    for e in text_entities:
        try:
            box = bbox.extents([e])
            if not box.has_data:
                continue
            cx = (box.extmin.x + box.extmax.x) / 2
            cy = (box.extmin.y + box.extmax.y) / 2
        except Exception:
            continue
        text = _entity_plain_text(e).strip()
        if not text:
            continue
        # TEXT 取左下 x；MTEXT 取插入点 x
        try:
            x0 = float(e.dxf.get("insert", (cx, 0, 0))[0]) if e.dxftype() == "MTEXT" else float(e.dxf.insert.x)
        except Exception:
            x0 = cx
        items.append((x0, cy, text))
    items.sort(key=lambda t: (-t[1], t[0]))  # y 降序，x 升序

    # 按 y 容差聚类
    if not items:
        return []
    avg_h = 5.0
    # 用第 1/2 项 y 差作为初始行高
    if len(items) >= 2:
        avg_h = max(1.0, abs(items[0][1] - items[1][1]))
    tol = max(0.5 * avg_h, 0.5)
    bucket = [items[0]]
    for it in items[1:]:
        if abs(it[1] - bucket[-1][1]) <= tol:
            bucket.append(it)
        else:
            bucket.sort(key=lambda t: t[0])
            rows.append(bucket)
            bucket = [it]
    if bucket:
        bucket.sort(key=lambda t: t[0])
        rows.append(bucket)
    # 转换为 List[List[str]]
    return [[t[2] for t in row] for row in rows if len(row) >= 2]


def _collect_sheet(sheet_path: str, sheet_id: str) -> SheetAnnotations:
    """从单张 sheet dxf 抽取标注聚合与几何统计。"""
    ann = SheetAnnotations(sheet_id=sheet_id)
    try:
        doc = ezdxf.readfile(sheet_path)
    except Exception as exc:
        log.warning("规则管线读 sheet 失败：%s err=%s", sheet_path, exc)
        return ann
    layout = doc.modelspace()

    layer_counter: Counter = Counter()
    for entity in layout:
        try:
            layer = entity.dxf.layer or ""
            layer_counter[layer] += 1
        except Exception:
            pass
        t = entity.dxftype()
        if t in ("TEXT", "MTEXT"):
            txt = _entity_plain_text(entity).strip()
            if txt:
                ann.texts.append(txt)
        elif t == "CIRCLE":
            ann.circle_count += 1
        elif t == "INSERT":
            ann.insert_count += 1

    ann.layer_counts = dict(layer_counter)
    ann.table_rows = _approx_table_rows(layout)
    return ann


# --------------------------------------------------------------------------
# 分类
# --------------------------------------------------------------------------

def _classify_sheet(ann: SheetAnnotations) -> Dict[str, float]:
    """基于关键字 + 图层给每个类别打分（0~1）。返回类别→分。"""
    scores: Dict[str, float] = {c: 0.0 for c in CATEGORIES}
    blob = "\n".join(ann.texts + [" ".join(r) for r in ann.table_rows])
    blob_l = blob.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in blob_l:
                scores[cat] += 0.6
    for layer, cat in LAYER_HINTS.items():
        if layer in ann.layer_counts:
            scores[cat] += min(0.4, ann.layer_counts[layer] / max(1, sum(ann.layer_counts.values())))

    # 几何兜底：圆多 + 块多 + 没有墙类关键字 → 偏向钻孔灌注桩
    if ann.circle_count >= 8 and scores["bored_pile"] == 0 and scores["diaphragm_wall"] == 0:
        scores["bored_pile"] += 0.3
    if ann.circle_count >= 20:
        scores["bored_pile"] += 0.2

    return scores


# --------------------------------------------------------------------------
# 字段解析
# --------------------------------------------------------------------------

def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _to_mm(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").lower()
    if u in ("mm", "毫米"):
        return value
    return value * 1000.0


def _to_m(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").lower()
    if u in ("m", "米"):
        return value
    return value / 1000.0


def _fill_an_n(texts: Iterable[str]) -> Optional[int]:
    """从「N=12 / 数量:24」等 KV 中取数量。"""
    for t in texts:
        m = RE_COUNT_KV.search(t)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    return None


def _fill_diameter(texts: Iterable[str]) -> Optional[float]:
    """从 Φ800 / 800mm 取桩径（mm）。"""
    for t in texts:
        m = RE_DIAMETER.search(t)
        if m:
            try:
                v = int(m.group(1))
                if 100 <= v <= 4000:
                    return float(v)
            except Exception:
                pass
    return None


def _fill_length(texts: Iterable[str], default_unit: str = "m") -> Optional[float]:
    for t in texts:
        m = RE_LENGTH.search(t)
        if m:
            return _to_m(float(m.group(1)), m.group(2) or default_unit)
    return None


def _fill_width(texts: Iterable[str], default_unit: str = "m") -> Optional[float]:
    for t in texts:
        m = RE_WIDTH.search(t)
        if m:
            return _to_m(float(m.group(1)), m.group(2) or default_unit)
    return None


def _fill_depth(texts: Iterable[str], default_unit: str = "m") -> Optional[float]:
    for t in texts:
        m = RE_DEPTH.search(t)
        if m:
            return _to_m(float(m.group(1)), m.group(2) or default_unit)
    return None


def _fill_volume(texts: Iterable[str]) -> Optional[float]:
    for t in texts:
        m = RE_VOLUME_KV.search(t)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return None


def _fill_cap_code(texts: Iterable[str]) -> Optional[str]:
    for t in texts:
        m = RE_CAP_CODE.search(t)
        if m:
            return m.group(1).strip()
    return None


def _fill_type(texts: Iterable[str]) -> Optional[str]:
    """围护桩桩型粗分类：钻孔灌注 / 挖孔 / 预制 / 搅拌。"""
    for t in texts:
        if "钻孔" in t:
            return "钻孔灌注"
        if "挖孔" in t:
            return "挖孔"
        if "预制" in t:
            return "预制"
        if "搅拌" in t or "SMW" in t.upper():
            return "搅拌"
        if "钢板" in t:
            return "钢板"
    return None


def _fill_shape(texts: Iterable[str]) -> Optional[str]:
    for t in texts:
        if "矩形" in t:
            return "矩形"
        if "圆形" in t or "圆柱" in t:
            return "圆形"
        if "方形" in t:
            return "方形"
        if "异形" in t:
            return "异形"
    return None


# 表面积 / 体积兜底估算
def _approx_circle_area_mm2(diameter_mm: Optional[float]) -> Optional[float]:
    if not diameter_mm:
        return None
    r = diameter_mm / 2000.0  # m
    return round(3.14159265 * r * r, 4)


def _approx_pile_volume_m3(
    count: Optional[int],
    diameter_mm: Optional[float],
    length_m: Optional[float],
) -> Optional[float]:
    """单根桩体积 = π r² h，合计 = 数量 × 单根。"""
    if not (count and diameter_mm and length_m):
        return None
    r = diameter_mm / 2000.0
    single = 3.14159265 * r * r * length_m
    return round(single * count, 4)


def _approx_panel_volume_m3(
    length_m: Optional[float],
    width_m: Optional[float],
    depth_m: Optional[float],
    panel_count: Optional[int],
) -> Optional[float]:
    if not (length_m and width_m and depth_m):
        return None
    single = length_m * width_m * depth_m
    if panel_count:
        return round(single * panel_count, 4)
    return round(single, 4)


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def _parse_field(
    category: str,
    field_name: str,
    ann: SheetAnnotations,
) -> Tuple[Optional[Any], str]:
    """按字段名 + 类别返回 (value, source)。

    source 优先级：annotation（最具体）> table（次级）> 无。
    这里把「同字段同时落到 KV 与 table 上」视为同一来源 annotation，
    因为表格行的字段通常本身也是 KV 形式。
    """
    sources: List[Tuple[str, Iterable[str]]] = [
        ("annotation", ann.texts),
        ("table", [" ".join(r) for r in ann.table_rows]),
    ]
    sources_priority = ["annotation", "table"]

    if field_name == "count":
        for src, items in sources:
            v = _fill_an_n(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "diameter":
        for src, items in sources:
            v = _fill_diameter(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "length":
        for src, items in sources:
            v = _fill_length(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "width":
        for src, items in sources:
            v = _fill_width(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "depth":
        for src, items in sources:
            v = _fill_depth(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "volume":
        for src, items in sources:
            v = _fill_volume(items)
            if v is not None:
                return v, src
        # 几何兜底：用 annotation 拿到的 count / diameter / length 推
        v = _approx_pile_volume_m3(
            _fill_an_n(ann.texts),
            _fill_diameter(ann.texts),
            _fill_length(ann.texts),
        )
        if v is not None:
            return v, "geometry"
        return None, ""
    if field_name == "type":
        for src, items in sources:
            v = _fill_type(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "shape":
        for src, items in sources:
            v = _fill_shape(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "code":
        for src, items in sources:
            v = _fill_cap_code(items)
            if v is not None:
                return v, src
        return None, ""
    if field_name == "group":
        # 连续墙分组：KW "第X组" / "第一段"
        for src, items in sources:
            for t in items:
                m = re.search(r"第\s*([一二三四五六七八九十0-9]+)\s*[组段]", t)
                if m:
                    return m.group(1), src
        return None, ""
    if field_name == "panel":
        # 复用 count 解析
        return _parse_field(category, "count", ann)
    if field_name == "area":
        # 围护桩/钻孔桩：截面积 = π × r²
        if category in ("retaining_pile", "bored_pile"):
            v = _approx_circle_area_mm2(_fill_diameter(ann.texts))
            if v is not None:
                return v, "geometry"
        # 承台：粗略按矩形宽×长，未知则为 None
        return None, ""
    if field_name == "thickness":
        # 注意：annotation 没显式 KV 时，width/depth/length 的「第二个数」
        # 通常就是厚度；这里优先用字面 "厚度h=…" / "h=…" 的 KV。
        for src, items in sources:
            pat = re.compile(
                r"(?:厚(?:度)?|h)\s*[:=＝]?\s*(\d+(?:\.\d+)?)\s*(m|mm)?",
                re.IGNORECASE,
            )
            for it in items:
                mm = pat.search(it)
                if mm:
                    return _to_m(float(mm.group(1)), mm.group(2) or "m"), src
        return None, ""
    return None, ""


def _infer_rows_for_sheet(
    ann: SheetAnnotations,
    scores: Dict[str, float],
) -> List[RuleDraftRow]:
    """根据一张 sheet 的标注聚合，给它命中的所有类别各生成一行 RuleDraftRow。

    字段缺失则 value=null、source=空串，方便三方对账显示「缺」。
    """
    rows: List[RuleDraftRow] = []
    for cat in CATEGORIES:
        if scores.get(cat, 0.0) < 0.3:
            continue
        row = RuleDraftRow(id=f"{ann.sheet_id}:{cat}")
        for field_name, label, unit, parser_key in CATEGORY_FIELDS[cat]:
            value, source = _parse_field(cat, field_name, ann)
            row.fields[field_name] = {
                "value": value,
                "source": source or "",
            }
        rows.append(row)
    return rows


def run_rules_pipeline(
    sheet_dir: str,
    sheet_ids: Optional[List[str]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> RuleDraftDict:
    """阶段① 主入口：对 sheet_dir 下所有 sheet 跑规则管线，输出 RuleDraft。

    :param sheet_dir: 拆分后 sheet 目录（含 manifest.json）
    :param sheet_ids: 仅跑列表中的 sheet（None=全部）
    :param on_progress: 进度回调，签名 (i, total)
    """
    import json

    if not os.path.isdir(sheet_dir):
        raise FileNotFoundError(f"找不到 sheet 目录：{sheet_dir}")
    manifest_path = os.path.join(sheet_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"找不到 manifest.json：{manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as fp:
        manifest = json.load(fp)
    sheets = manifest.get("sheets") or []

    by_category: Dict[str, List[RuleDraftRow]] = {c: [] for c in CATEGORIES}
    summary_hits: List[Dict[str, Any]] = []
    total = len(sheets)
    for i, sh in enumerate(sheets):
        sheet_id = sh.get("id") or f"sheet_{i+1}"
        if sheet_ids is not None and sheet_id not in sheet_ids:
            if on_progress:
                try:
                    on_progress(i + 1, total)
                except Exception:
                    pass
            continue
        sheet_path = os.path.join(sheet_dir, f"{sheet_id}.dxf")
        if not os.path.isfile(sheet_path):
            log.warning("缺少 sheet 文件：%s", sheet_path)
            continue
        ann = _collect_sheet(sheet_path, sheet_id)
        ann.sheet_name = sh.get("name") or sheet_id
        scores = _classify_sheet(ann)
        rows = _infer_rows_for_sheet(ann, scores)
        hits = {c: round(scores.get(c, 0.0), 3) for c in CATEGORIES if scores.get(c, 0) > 0}
        for r in rows:
            cat = r.id.split(":")[1]
            by_category[cat].append(r)
        summary_hits.append({"sheetId": sheet_id, "sheetName": ann.sheet_name, "hits": hits})
        if on_progress:
            try:
                on_progress(i + 1, total)
            except Exception:
                pass

    summary = {
        "sheetCount": total,
        "rowsByCategory": {c: len(by_category[c]) for c in CATEGORIES},
        "bySheet": summary_hits,
    }
    return RuleDraftDict(
        byCategory=by_category,
        summary=summary,
    )


# --------------------------------------------------------------------------
# JSON 兼容的 to_dict
# --------------------------------------------------------------------------

def rule_draft_to_json(draft: RuleDraftDict) -> Dict[str, Any]:
    return {
        "byCategory": {
            cat: [
                {"id": row.id, "fields": row.fields}
                for row in draft.byCategory.get(cat, [])
            ]
            for cat in CATEGORIES
        },
        "summary": draft.summary,
    }


__all__ = [
    "CATEGORIES",
    "CATEGORY_FIELDS",
    "SheetAnnotations",
    "RuleDraftRow",
    "RuleDraftDict",
    "run_rules_pipeline",
    "rule_draft_to_json",
]