# -*- coding: utf-8 -*-
"""图框检测与图名提取。

检测思路：
1. 闭合的 4 点 LWPOLYLINE / 旧式 POLYLINE，其外接矩形尺寸匹配 A0–A4 标准图幅；
2. INSERT 块参照：
   - 块名含强关键字（图框/TK/TITLE/FRAME）→ 直接作为候选；
   - 块名仅含 A0–A4 弱关键字 → 要求几何短边接近标准图幅短边，避免随机块名
     （如 A$C65FA1CB7 含子串 "A1"）被误判；
   - 其余 INSERT 的包围盒尺寸匹配标准图幅 → 候选；
3. 图纸空间 VIEWPORT：用 ezdxf 的 get_modelspace_limits() 得到视口在
   模型空间坐标系下的覆盖矩形，把该矩形作为图框，并把模型空间中落进
   该矩形的所有实体归入此视口。这是图纸空间布局里"显示出来的"内容
   实际所在——dxf-viewer 看不到视口引用的模型空间内容，必须在这里
   主动把它们复制到目标文档。
4. IoU 去重、嵌套包含去重；
5. 候选为空时回退为整图一张，名称为「全图」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Set, Tuple

import ezdxf
from ezdxf import bbox
from ezdxf.entities import Insert, LWPolyline, Polyline, Viewport
from ezdxf.math import Matrix44

# 标准图幅：名称 → (长边, 短边)，单位 mm
STANDARD_SIZES = {
    "A0": (1189, 841),
    "A1": (841, 594),
    "A2": (594, 420),
    "A3": (420, 297),
    "A4": (297, 210),
}

# 强关键字：块名命中即视为图框
STRONG_KEYWORDS = ("图框", "TK", "TITLE", "FRAME")
# 弱关键字：命中后仍需几何尺寸校验
WEAK_KEYWORDS = ("A0", "A1", "A2", "A3", "A4")

# 常见出图比例：弱关键字块的短边缩放值应落在这些比例附近，
# 用于排除 A$Cdaaa0768（缩放 0.0125）等随机命名的小构件块
COMMON_SCALES = (
    0.1, 0.2, 0.5,
    1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000,
)

# 图名属性标签（大写比较）
TITLE_TAGS = ("图名", "图纸名称", "TITLE", "DWGNAME")

# 矩形类型：(min_x, min_y, max_x, max_y)
Rect = Tuple[float, float, float, float]


# 轻量 _Vec3 / _BoxView：替代 ezdxf.bbox.Extents（不存在），让
# _insert_world_bbox 走块定义级缓存时可返回与 ezdxf BoundingBox 等价的
# 对象，避免每个 INSERT 都做一次 list(virtual_entities()).
@dataclass
class _Vec3:
    x: float
    y: float
    z: float = 0.0


@dataclass
class _BoxView:
    extmin: Any
    extmax: Any
    size: Any
    has_data: bool = True


@dataclass
class Frame:
    """一张检测到的图框。"""

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    source_kind: str          # polyline / block / viewport / fallback
    layout_name: str = "Model"  # 所属布局名（模型空间 / 图纸空间）
    name: Optional[str] = None
    source_entity: Any = None  # 来源图元（INSERT / 多段线），内部使用
    # 视口在模型空间坐标系下的覆盖矩形 (min_x, min_y, max_x, max_y)。
    # source_kind=="viewport" 时是该视口自身的覆盖；
    # 标题块帧（block/polyline）在去重时会吞并其几何包含的视口，
    # 此时为所有内含视口 ms 覆盖的并集，用于拉取模型空间图纸内容。
    viewport_ms_bounds: Optional[Rect] = None
    # 仅标题块帧使用：内含视口列表，每项为 (ms 覆盖矩形, VIEWPORT 图元)，
    # 导出时按各视口自身的模型→图纸变换矩阵分别搬运模型图元。
    contained_viewports: Optional[list] = None

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y


def matches_standard_size(width: float, height: float) -> bool:
    """判断给定宽高是否匹配某一标准图幅（允许等比缩放与横/竖方向）。"""
    long_edge = max(width, height)
    # 保护：长边至少 100（图纸单位），避免小矩形误判
    if long_edge < 100:
        return False

    for std_long, std_short in STANDARD_SIZES.values():
        # 两种映射：width→长边/height→短边，以及 width→短边/height→长边
        for ref_w, ref_h in (
            (width, height),
            (height, width),
        ):
            scale = ref_w / std_long
            if scale <= 0:
                continue
            if abs(scale * std_short - ref_h) / std_short <= 0.02:
                return True
    return False


def _short_edge_matches_standard(width: float, height: float) -> bool:
    """校验短边是否为某标准图幅短边按常见出图比例等比缩放的结果。

    用于弱关键字候选：加长图幅只加长长边，短边仍符合标准；
    同时要求缩放值接近常见出图比例（1/10/100…），
    避免随机块名（如 A$Cdaaa0768 含子串 "A4"）的小构件误判。
    """
    short = min(width, height)
    if short < 100:
        return False
    # 标准图幅宽高比最小约 1.41（297/210），加长图幅更大；
    # 接近 1:1 的矩形（如 300×300 小构件）必非图框
    if max(width, height) / short < 1.35:
        return False
    for _std_long, std_short in STANDARD_SIZES.values():
        scale = short / std_short
        if any(abs(scale - common) / common <= 0.02
               for common in COMMON_SCALES):
            return True
    return False


def _vertices_at_corners(
    xs: List[float], ys: List[float], rect: Rect
) -> bool:
    """校验 4 个顶点确实位于外接矩形的四角附近（容差为长宽的 1%）。"""
    min_x, min_y, max_x, max_y = rect
    tol = 0.01 * max(max_x - min_x, max_y - min_y)
    corners = {
        (min_x, min_y),
        (min_x, max_y),
        (max_x, min_y),
        (max_x, max_y),
    }
    for x, y in zip(xs, ys):
        if not any(
            abs(x - cx) <= tol and abs(y - cy) <= tol for cx, cy in corners
        ):
            return False
    return True


def _block_definition_bbox(block_def) -> Optional[Any]:
    """求块定义在「块局部坐标系」下的几何包围盒（不含 INSERT 变换）。

    优先读 ezdxf 的内部缓存 bbox（O(1)）；缓存为空时才走
    virtual_entities() + bbox.extents（O(块大小）），并把结果写回缓存。

    对同一块定义重复求 bbox 的常见情况（同一图框块被多次 INSERT），
    第一次计算后所有后续调用都从缓存读取——这是 detect_frames 在
    含大量重复图框块的桩位图上能快几倍的关键。

    入参兼容 Insert 与 BlockLayout：Insert.block() 返回 BlockLayout；
    实际计算需要 BlockRecord（在 blocks_table.block_records 中）。
    """
    if block_def is None:
        return None
    try:
        cached = getattr(block_def, "_frame_bbox_cache", None)
        if cached is not None:
            return cached

        # 取 BlockRecord（拥有 virtual_entities / bbox）
        record = getattr(block_def, "block_records_owner", None)
        if record is None:
            # Insert.block() 返回 BlockLayout：从 owner doc 拿 block_records
            owner = getattr(block_def, "doc", None) or getattr(
                block_def, "_doc", None)
            if owner is not None:
                # 通过 layout 名（BlockLayout.name）查 BlockRecord
                name = getattr(block_def, "name", None)
                if name is not None and hasattr(owner, "blocks"):
                    rec_table = getattr(
                        owner.blocks, "block_records", None)
                    if rec_table is not None:
                        record = rec_table.get(name)
            if record is None and hasattr(block_def, "block_layout"):
                # BlockRecord 上的反向引用
                record = block_def

        # 优先走 ezdxf 内部缓存 bbox（最便宜的读取路径）
        for src in (record, block_def):
            if src is None:
                continue
            extents = getattr(src, "bbox", None)
            if extents is None:
                extents = getattr(src, "extents", None)
            if extents is not None and getattr(
                    extents, "has_data", False):
                block_def._frame_bbox_cache = extents
                return extents

        # 退回：列出 block_layout 中的 entity 并算包围盒
        entities = []
        try:
            # BlockLayout 是 iterable 的 entity 容器
            for ent in block_def:
                entities.append(ent)
        except Exception:
            pass
        if not entities:
            # 最后一次尝试：BlockRecord.virtual_entities
            try:
                if record is not None and hasattr(
                        record, "virtual_entities"):
                    entities = list(record.virtual_entities())
            except Exception:
                entities = []
        if not entities:
            return None
        box = bbox.extents(entities)
        if box.has_data:
            block_def._frame_bbox_cache = box
            return box
    except Exception:
        pass
    return None


def _insert_world_bbox(insert: Insert):
    """求 INSERT 的世界包围盒。

    优化：用块定义在自身坐标系下的 bbox（命中块定义级缓存），
    再叠加 INSERT 的插入点 (insert.x, insert.y) 与 (xscale, yscale)
    以及旋转（旋转不改变包围盒面积与长短边，仅改变朝向）。

    对比 v1：本函数原本每次都对同一个 INSERT 调用 list(virtual_entities())
    并重算 extents，遇到同一块被多次插入时会重复做 O(块大小) 工作。
    改用块定义级缓存后，整体复杂度从 O(N · B) 降到 O(B + N)。
    """
    block_def = insert.block()
    box = _block_definition_bbox(block_def)
    if box is None:
        return None

    try:
        sx = float(insert.dxf.get("xscale", 1.0) or 1.0)
        sy = float(insert.dxf.get("yscale", 1.0) or 1.0)
    except Exception:
        sx, sy = 1.0, 1.0
    insert_pt = insert.dxf.insert
    # 块基点：DXF 插入时基点对齐到插入点，块内点 p 的世界坐标为
    # insert + scale*(p - base_point)。漏掉基点平移会整体错位（如 A0
    # 标题块基点在 (1189,0)，bbox 会被错放到图框右侧整整一个图宽）。
    base_x = base_y = 0.0
    block_entity = getattr(block_def, "block", None)
    if block_entity is not None:
        try:
            bp = block_entity.dxf.get("base_point", None)
            if bp is not None:
                base_x, base_y = float(bp[0]), float(bp[1])
        except Exception:
            pass
    # sx/sy 为负 = 镜像。把块定义包围盒的两个角点（box.extmin / box.extmax）
    # 各自「减基点后再缩放」分别比较，不能用「中心 ± |缩放|*半宽」重建——
    # 镜像时中心方向已翻转，而半宽仍取绝对值会整体错位。
    x_a = insert_pt.x + (box.extmin.x - base_x) * sx
    x_b = insert_pt.x + (box.extmax.x - base_x) * sx
    y_a = insert_pt.y + (box.extmin.y - base_y) * sy
    y_b = insert_pt.y + (box.extmax.y - base_y) * sy
    min_x, max_x = (x_a, x_b) if x_a <= x_b else (x_b, x_a)
    min_y, max_y = (y_a, y_b) if y_a <= y_b else (y_b, y_a)

    # ezdxf 没有现成的 Extents 包装型，用模块级的 _BoxView 兼容原代码
    # 对 box.extmin / box.extmax / box.size / box.has_data 的访问。
    return _BoxView(
        extmin=_Vec3(min_x, min_y, 0.0),
        extmax=_Vec3(max_x, max_y, 0.0),
        size=_Vec3(max_x - min_x, max_y - min_y, 0.0),
        has_data=True,
    )


def _viewport_ms_center(vp: Viewport) -> Optional[Tuple[float, float]]:
    """视口取景中心在模型空间（WCS）中的坐标。

    LibreDWG 转出的 DXF 里，group 12（view_center_point）是相对取景
    目标的 DCS 偏移，group 17（view_target_point）是取景目标的 WCS
    坐标；真实取景中心为两者之和。ezdxf 的 get_view_center_point()
    只读 group 12，在这类文件上会整体错位，故这里手工求和。
    """
    try:
        vc = vp.dxf.view_center_point
        vt = vp.dxf.view_target_point
        return (vc[0] + vt[0], vc[1] + vt[1])
    except Exception:
        return None


def viewport_transform_matrix(vp: Viewport) -> Optional[Matrix44]:
    """视口的模型空间 → 图纸空间变换矩阵（top view）。

    与 ezdxf 的 get_transformation_matrix() 相同的构造方式，但取景中心
    改用 view_center_point + view_target_point（见 _viewport_ms_center），
    使 LibreDWG 转出的视口也能得到正确的平移。
    """
    try:
        center = _viewport_ms_center(vp)
        if center is None:
            return None
        scale = vp.get_scale()
        rotation = float(vp.dxf.get("view_twist_angle", 0.0) or 0.0)
        paper_center = vp.dxf.center
        offset = (
            paper_center.x - center[0] * scale,
            paper_center.y - center[1] * scale,
        )
        m = Matrix44.scale(scale)
        if rotation:
            import math
            m @= Matrix44.z_rotate(math.radians(rotation))
        return m @ Matrix44.translate(offset[0], offset[1], 0.0)
    except Exception:
        return None


def _viewport_ms_rect(vp: Viewport) -> Optional[Rect]:
    """返回视口在模型空间坐标系下的覆盖矩形 (min_x, min_y, max_x, max_y)。

    仅当存在有效数据且矩形面积 > 0 时返回，否则返回 None。
    """
    try:
        center = _viewport_ms_center(vp)
        if center is None:
            return None
        view_height = float(vp.dxf.view_height)
        vp_w = float(vp.dxf.width)
        vp_h = float(vp.dxf.height)
        if view_height <= 0 or vp_w <= 0 or vp_h <= 0:
            return None
        view_width = view_height * vp_w / vp_h
        cx, cy = center
        return (
            cx - view_width / 2, cy - view_height / 2,
            cx + view_width / 2, cy + view_height / 2,
        )
    except Exception:
        return None


def _viewport_rect(vp: Viewport) -> Optional[Rect]:
    """视口在图纸空间（自身布局）中的显示矩形。

    用视口自身的 width/height 与 center 重建，避免直接采用
    viewport_ms_bounds（模型空间坐标）造成图框被"放大"成几千米长。
    """
    try:
        width = float(vp.dxf.width)
        height = float(vp.dxf.height)
        center = vp.dxf.center
        if width <= 0 or height <= 0:
            return None
        cx, cy = center.x, center.y
        return (cx - width / 2, cy - height / 2,
                cx + width / 2, cy + height / 2)
    except Exception:
        return None


def _viewport_candidate(entity: Viewport) -> Optional[Frame]:
    """把单个 VIEWPORT 图元转换为图框候选。

    仅在当前布局是图纸空间时才有意义（模型空间里出现的 Viewport
    实为旧式 R12 视口，跳过）。

    视口几何矩形采用 *自身* 的 width/height + center 重建，
    而非直接用 viewport_ms_bounds（模型空间坐标）：模型空间
    坐标通常与图纸世界坐标存在巨大比例差异（如 1:100 出图时
    视口在模型空间覆盖 100 米 × 70 米，但屏幕几何仅为 1 米×0.7 米），
    否则图框矩形会被错误放大、产生 5230 × 53386 这类长条矩形。

    viewport_ms_bounds 仍用作「归集模型空间实体」的查找窗口，
    与几何矩形分别承担不同职责。
    """
    ms_rect = _viewport_ms_rect(entity)
    if ms_rect is None:
        return None
    rect = _viewport_rect(entity)
    if rect is None:
        # 视口 width/height 缺失或非法：仅当 ms_rect 自身匹配
        # 标准图幅时才回退使用，否则视为不可用。
        if matches_standard_size(
            ms_rect[2] - ms_rect[0],
            ms_rect[3] - ms_rect[1],
        ):
            rect = ms_rect
        else:
            return None
    min_x, min_y, max_x, max_y = rect
    frame = Frame(
        min_x, min_y, max_x, max_y,
        "viewport", source_entity=entity,
    )
    frame.viewport_ms_bounds = ms_rect
    return frame


def _collect_candidates_in_layout(layout) -> List[Frame]:
    """遍历单个布局收集图框候选。"""
    candidates: List[Frame] = []

    for entity in layout:
        try:
            frame = _entity_to_candidate(entity)
            if frame is not None:
                frame.layout_name = layout.name
                candidates.append(frame)
        except Exception:
            # 单个坏图元不应影响整体检测
            continue

    return candidates


def _entity_to_candidate(entity) -> Optional[Frame]:
    """把单个图元转换为图框候选，不满足条件返回 None。"""
    if isinstance(entity, LWPolyline):
        return _lwpolyline_candidate(entity)
    if isinstance(entity, Polyline):
        return _old_polyline_candidate(entity)
    if isinstance(entity, Insert):
        return _insert_candidate(entity)
    if isinstance(entity, Viewport):
        return _viewport_candidate(entity)
    return None


def _make_rect_frame(xs: List[float], ys: List[float], entity) -> Optional[Frame]:
    """由顶点坐标生成经过四角校验与尺寸校验的多段线图框候选。"""
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    rect = (min_x, min_y, max_x, max_y)
    if max_x - min_x <= 0 or max_y - min_y <= 0:
        return None
    if not _vertices_at_corners(xs, ys, rect):
        return None
    if not matches_standard_size(max_x - min_x, max_y - min_y):
        return None
    return Frame(min_x, min_y, max_x, max_y, "polyline",
                 source_entity=entity)


def _lwpolyline_candidate(entity: LWPolyline) -> Optional[Frame]:
    points = list(entity.get_points())
    if not entity.is_closed or len(points) != 4:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return _make_rect_frame(xs, ys, entity)


def _old_polyline_candidate(entity: Polyline) -> Optional[Frame]:
    # 仅处理 2D 多段线：排除 3D 多段线与多边形网格
    if entity.is_3d_polyline or entity.is_polygon_mesh:
        return None
    if not entity.is_closed:
        return None
    vertices = list(entity.vertices())
    if len(vertices) != 4:
        return None
    locations = [v.dxf.location for v in vertices]
    xs = [p.x for p in locations]
    ys = [p.y for p in locations]
    return _make_rect_frame(xs, ys, entity)


def _insert_candidate(entity: Insert) -> Optional[Frame]:
    name = entity.dxf.name.upper()
    box = _insert_world_bbox(entity)

    is_strong = any(k in name for k in STRONG_KEYWORDS)
    is_weak = any(k in name for k in WEAK_KEYWORDS)

    if is_strong:
        # 强关键字：直接候选（不要求尺寸匹配）
        if box is not None:
            return Frame(
                box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y,
                "block", source_entity=entity,
            )
        # 极端情况下包围盒求解失败，退化为插入点
        x, y = entity.dxf.insert.x, entity.dxf.insert.y
        return Frame(x, y, x, y, "block", source_entity=entity)

    if is_weak:
        # 弱关键字：要求短边接近标准图幅短边
        if box is not None and _short_edge_matches_standard(
            box.size.x, box.size.y
        ):
            return Frame(
                box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y,
                "block", source_entity=entity,
            )
        return None

    # 无关键字：尺寸匹配标准图幅时，仅接受匿名块以外的图框样式块。
    # 仅靠尺寸会把图纸里的表格（如「层高表」5978×4239 恰好≈A2×10）、
    # CAD 自动生成的匿名块（A$C 开头）误判成图框。
    if (
        box is not None
        and matches_standard_size(box.size.x, box.size.y)
        and not name.startswith("A$C")
        and _looks_like_frame_block_name(name)
    ):
        return Frame(
            box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y,
            "block", source_entity=entity,
        )
    return None


def _looks_like_frame_block_name(name: str) -> bool:
    """块名是否带图框/图签语义（无关键字兜底分支使用）。"""
    return any(
        token in name
        for token in (
            "图框", "标题", "图签", "图幅",
            "TK", "TITLE", "FRAME", "BORDER",
        )
    )


def _rect_area(rect: Rect) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _union_rect(a: Optional[Rect], b: Optional[Rect]) -> Optional[Rect]:
    """两个矩形的并集包围矩形；任一为 None 时返回另一个。"""
    if a is None:
        return b
    if b is None:
        return a
    return (
        min(a[0], b[0]), min(a[1], b[1]),
        max(a[2], b[2]), max(a[3], b[3]),
    )


def _iou(a: Frame, b: Frame) -> float:
    """两个矩形的交并比。"""
    x1 = max(a.min_x, b.min_x)
    y1 = max(a.min_y, b.min_y)
    x2 = min(a.max_x, b.max_x)
    y2 = min(a.max_y, b.max_y)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a.width * a.height) + (b.width * b.height) - inter
    return inter / union if union > 0 else 0.0


def _contains(outer: Frame, inner: Frame) -> bool:
    """判断 inner 是否被 outer 完全包含（边界容差为外框长宽的 1%）。"""
    tol = 0.01 * max(outer.width, outer.height, 1.0)
    return (
        outer.min_x - tol <= inner.min_x
        and outer.min_y - tol <= inner.min_y
        and inner.max_x <= outer.max_x + tol
        and inner.max_y <= outer.max_y + tol
    )


def _viewport_ms_iou(a: Frame, b: Frame) -> Optional[float]:
    """两个视口帧在模型空间覆盖矩形上的 IoU；任一非视口返回 None。"""
    ra = getattr(a, "viewport_ms_bounds", None)
    rb = getattr(b, "viewport_ms_bounds", None)
    if ra is None or rb is None:
        return None
    x1 = max(ra[0], rb[0])
    y1 = max(ra[1], rb[1])
    x2 = min(ra[2], rb[2])
    y2 = min(ra[3], rb[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = _rect_area(ra) + _rect_area(rb) - inter
    return inter / union if union > 0 else 0.0


def _deduplicate(frames: List[Frame]) -> List[Frame]:
    """IoU 去重 + 嵌套包含去重。"""
    # IoU 合并：几乎重合视为同一图框，polyline 优先于 block
    merged: List[Frame] = []
    for frame in frames:
        hit = None
        for i, other in enumerate(merged):
            if _iou(frame, other) >= 0.9:
                hit = i
                break
        if hit is None:
            merged.append(frame)
        elif frame.source_kind == "polyline" and merged[hit].source_kind != "polyline":
            merged[hit] = frame

    # Pass A：视口 → 标题块吸收。
    # 一个视口若被某个 block/polyline 候选严格包含，它就不是独立图纸，
    # 而是该标题块图纸的取景器（图纸内容在模型空间）。先把这层关系
    # 全部挂好，再处理嵌套丢弃——否则总览视口会先把标题块帧丢掉，
    # 导致内含视口无人接收。
    absorbed: Set[int] = set()
    for i, frame in enumerate(merged):
        if frame.source_kind != "viewport":
            continue
        host = None
        host_area = None
        for j, other in enumerate(merged):
            if i == j or other.source_kind == "viewport":
                continue
            outer_area = other.width * other.height
            inner_area = frame.width * frame.height
            if not (_contains(other, frame) and outer_area > inner_area * 1.01):
                continue
            # 多个标题块候选都包含时取最小的（真正的边框）
            if host is None or outer_area < host_area:
                host = j
                host_area = outer_area
        if host is None:
            continue
        other = merged[host]
        if other.contained_viewports is None:
            other.contained_viewports = []
        other.contained_viewports.append(
            (frame.viewport_ms_bounds, frame.source_entity)
        )
        other.viewport_ms_bounds = _union_rect(
            other.viewport_ms_bounds, frame.viewport_ms_bounds
        )
        absorbed.add(i)

    # Pass B：嵌套包含丢弃。
    keep: List[Frame] = []
    for i, frame in enumerate(merged):
        # 已被标题块吸收的视口不独立成帧
        if i in absorbed:
            continue
        contained = False
        for j, other in enumerate(merged):
            if i == j or j in absorbed:
                continue
            outer_area = other.width * other.height
            inner_area = frame.width * frame.height
            # 仅当外框明显更大时才视为包含，避免等大矩形互相吞掉
            if not (_contains(other, frame) and outer_area > inner_area * 1.01):
                continue
            if frame.source_kind == "viewport" and other.source_kind == "viewport":
                # 视口嵌套视口（典型：总览视口包着图纸视口）不能直接
                # 丢内层：只有模型空间覆盖也几乎相同才算冗余。
                ms_iou = _viewport_ms_iou(other, frame)
                if ms_iou is not None and ms_iou < 0.9:
                    continue
            elif (
                other.source_kind == "viewport"
                and getattr(frame, "contained_viewports", None)
            ):
                # 总览视口包着的是带视口的标题块帧：这些是真正的图纸，
                # 总览视口才是应被丢弃的取景器。
                continue
            contained = True
            break
        if not contained:
            keep.append(frame)

    # 总览视口：一个独立视口严格包含所有其它保留帧时，它只是图纸空间
    # 的整体预览取景器，不是独立图纸。
    for i, frame in enumerate(keep):
        if frame.source_kind != "viewport" or frame.contained_viewports:
            continue
        others = [o for o in keep if o is not frame]
        if not others:
            continue
        all_inside = all(
            _contains(frame, o)
            and frame.width * frame.height > o.width * o.height * 1.01
            for o in others
        )
        if all_inside:
            del keep[i]
            break

    return keep


def _get_attrib_title(insert: Insert) -> Optional[str]:
    """从 INSERT 的 ATTRIB 中读取图名。"""
    try:
        for attrib in insert.attribs:
            tag = attrib.dxf.tag.upper()
            if any(t in tag for t in TITLE_TAGS):
                text = (attrib.dxf.text or "").strip()
                if text:
                    return text
    except Exception:
        pass
    return None


def _entity_text_height(entity) -> float:
    """读取文字高度，MTEXT 缺 char_height 时给默认 2.5。"""
    try:
        if entity.dxftype() == "MTEXT":
            return float(entity.dxf.get("char_height", 2.5) or 2.5)
        return float(entity.dxf.height)
    except Exception:
        return 2.5


def _entity_plain_text(entity) -> str:
    """读取文字内容。"""
    if entity.dxftype() == "MTEXT":
        try:
            return entity.plain_text()
        except Exception:
            try:
                return entity.text
            except Exception:
                return ""
    try:
        return entity.dxf.text
    except Exception:
        return ""


def _title_bar_rect(frame: Frame) -> Rect:
    """标题栏区域：图框下方 25% 与右侧 45% 的交集矩形。"""
    return (
        frame.min_x + frame.width * 0.55,
        frame.min_y,
        frame.max_x,
        frame.min_y + frame.height * 0.25,
    )


def _point_in_rect(x: float, y: float, rect: Rect) -> bool:
    min_x, min_y, max_x, max_y = rect
    return (
        min_x - 1e-6 <= x <= max_x + 1e-6
        and min_y - 1e-6 <= y <= max_y + 1e-6
    )


def _get_text_title(frame: Frame, text_entities) -> Optional[str]:
    """在标题栏区域内取字号最大的 TEXT/MTEXT。"""
    title_rect = _title_bar_rect(frame)
    best: Optional[Tuple[float, str]] = None
    for entity in text_entities:
        try:
            box = bbox.extents([entity])
            if not box.has_data:
                continue
            cx = (box.extmin.x + box.extmax.x) / 2
            cy = (box.extmin.y + box.extmax.y) / 2
            if not _point_in_rect(cx, cy, title_rect):
                continue
            height = _entity_text_height(entity)
            text = _entity_plain_text(entity).strip()
            if text and (best is None or height > best[0]):
                best = (height, text)
        except Exception:
            continue
    return best[1] if best else None


def _assign_names_in_layout(frames: List[Frame], layout) -> None:
    """为同一布局内的图框填充名称，并处理该布局内重名。

    viewport 类型图框没有独立图名来源（视口本身没有图名 ATTRIB），
    因此跳过它的 ATTRIB 提取，仅依赖标题栏文字 / 顺序命名。
    """
    # 全量文字只收集一次，作为标题栏文字数据源
    text_entities = [
        e for e in layout if e.dxftype() in ("TEXT", "MTEXT")
    ]

    for frame in frames:
        name = None
        if frame.source_kind == "block" and isinstance(
            frame.source_entity, Insert
        ):
            name = _get_attrib_title(frame.source_entity)
        if not name:
            name = _get_text_title(frame, text_entities)
        frame.name = name

    # None 按布局内顺序命名「图纸N」，N 不跳号
    index = 1
    for frame in frames:
        if not frame.name:
            frame.name = "图纸%d" % index
        index += 1

    # 布局内重名追加 -2、-3 …
    seen: dict = {}
    for frame in frames:
        base = frame.name
        if base not in seen:
            seen[base] = 1
        else:
            seen[base] += 1
            frame.name = "%s-%d" % (base, seen[base])


def _fallback_frame(layout) -> Optional[Frame]:
    """候选为空时，以整个布局几何范围生成 1 张图框。

    布局完全为空（无任何几何）时返回 None，以便跳过没有内容的
    默认图纸空间布局，不产生多余的图纸条目。
    """
    try:
        box = bbox.extents(layout, fast=True)
        if box.has_data and box.size.x > 0 and box.size.y > 0:
            return Frame(
                box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y,
                "fallback", layout_name=layout.name, name="全图",
            )
    except Exception:
        pass
    return None


def detect_frames(doc) -> List[Frame]:
    """检测模型空间及全部图纸空间布局中的图框。

    检测/去重/排序/命名均以布局为单位，不同布局互不影响；
    图纸空间布局的图纸名前追加「[布局名] 」前缀以区分同名图纸；
    没有任何内容的空布局直接跳过。
    """
    result: List[Frame] = []

    # 按标签顺序遍历，模型空间排在最前
    for name in doc.layout_names_in_taborder():
        layout = doc.layouts.get(name)
        candidates = _collect_candidates_in_layout(layout)
        frames = _deduplicate(candidates)

        if not frames:
            fallback = _fallback_frame(layout)
            if fallback is None:
                continue
            frames = [fallback]
        else:
            # 排序：先按 min_y 降序（从上到下），再按 min_x 升序（从左到右）
            frames.sort(key=lambda f: (-f.min_y, f.min_x))
            _assign_names_in_layout(frames, layout)
            if not getattr(layout, "is_modelspace", False):
                for frame in frames:
                    frame.name = "[%s] %s" % (name, frame.name)

        result.extend(frames)

    return result