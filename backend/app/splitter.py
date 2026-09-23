# -*- coding: utf-8 -*-
"""按图框把 DXF 文档拆分为多份独立图纸 DXF。

跨文档复制使用 ezdxf 官方的 ezdxf.xref 模块：ezdxf 不允许把一个文档的
图元直接加入另一个文档（add_entity / deepcopy 均会抛 DXFStructureError），
而 xref.load_modelspace 会把源文档图元连同其依赖的表资源（图层、线型、
文字样式、标注样式）与块定义一并导入目标文档，冲突资源按 ConflictPolicy
处理，无需手工全量复制块定义。
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, List, Optional, Set

import ezdxf
from ezdxf import bbox, xref

from .frames import Frame, viewport_transform_matrix


def _entity_anchor(entity) -> Optional[tuple]:
    """求图元归属点：优先包围盒中心，无包围盒时退回插入点。"""
    try:
        box = bbox.extents([entity])
        if box.has_data:
            return (
                (box.extmin.x + box.extmax.x) / 2,
                (box.extmin.y + box.extmax.y) / 2,
            )
    except Exception:
        pass

    # 无包围盒：INSERT / TEXT 等读取插入点
    try:
        if entity.dxf.hasattr("insert"):
            point = entity.dxf.insert
            return (point.x, point.y)
    except Exception:
        pass
    return None


def _entity_bbox(entity):
    """返回图元包围盒 (min_x, min_y, max_x, max_y)；求不出返回 None。

    不要求宽高均 > 0：水平/垂直的 LINE 等图元有一个方向为零，但几何
    位置真实存在，若丢弃包围盒会退回中心锚点，导致"穿过图框的长线"
    因中心落在框内而被错误保留。
    """
    try:
        box = bbox.extents([entity])
        if box.has_data and box.size.x >= 0 and box.size.y >= 0:
            return (
                box.extmin.x, box.extmin.y,
                box.extmax.x, box.extmax.y,
            )
    except Exception:
        pass
    return None


def _entity_signature(entity, tol: int = 6) -> tuple:
    """生成实体几何签名 (类型, 中心 x, 中心 y, 宽, 高)。

    用于在 xref 复制后把克隆对应回源实体（克隆 handle 会因冲突重号）。
    坐标四舍五入到 tol 位小数以消除浮点误差；求不出 bbox 时用插入点。
    """
    box = _entity_bbox(entity)
    if box is not None:
        return (
            entity.dxftype(),
            round((box[0] + box[2]) / 2, tol),
            round((box[1] + box[3]) / 2, tol),
            round(box[2] - box[0], tol),
            round(box[3] - box[1], tol),
        )
    anchor = _entity_anchor(entity)
    if anchor is not None:
        return (entity.dxftype(), round(anchor[0], tol),
                round(anchor[1], tol), 0.0, 0.0)
    return (entity.dxftype(), None, None, None, None)


def _point_in_frame(x: float, y: float, frame: Frame) -> bool:
    return (
        frame.min_x - 1e-6 <= x <= frame.max_x + 1e-6
        and frame.min_y - 1e-6 <= y <= frame.max_y + 1e-6
    )


def _point_in_rect(x: float, y: float, rect) -> bool:
    """点是否落在矩形内（含 1e-6 容差）。rect = (min_x, min_y, max_x, max_y)。"""
    min_x, min_y, max_x, max_y = rect
    return (
        min_x - 1e-6 <= x <= max_x + 1e-6
        and min_y - 1e-6 <= y <= max_y + 1e-6
    )


def _coverage_in_frame(
    ent_min_x: float, ent_min_y: float,
    ent_max_x: float, ent_max_y: float,
    rect,
) -> float:
    """图元包围盒落入图框矩形（或模型空间视口矩形）的比例。

    用于过滤"穿过图框"或"主体在图框外"的图元——只有大部分落在框内
    才算属于该图框。这是用户对"超过图框范围的图形去掉"的需求落地。

    rect 可以是 Frame 对象（自身拥有 min_x/min_y/max_x/max_y 属性），
    也可以是 (min_x, min_y, max_x, max_y) 四元组（用于模型空间视口矩形）。
    """
    try:
        rx1 = float(rect.min_x)
        ry1 = float(rect.min_y)
        rx2 = float(rect.max_x)
        ry2 = float(rect.max_y)
    except AttributeError:
        rx1, ry1, rx2, ry2 = rect
    ix1 = max(ent_min_x, rx1)
    iy1 = max(ent_min_y, ry1)
    ix2 = min(ent_max_x, rx2)
    iy2 = min(ent_max_y, ry2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ent_area = max(1e-9, (ent_max_x - ent_min_x) * (ent_max_y - ent_min_y))
    if inter > 0:
        return inter / ent_area
    # 退化图元（水平/竖直线、点）：面积法失效，按「落入长度 / 总长度」
    # 计算覆盖度，避免穿过图框的长线被当成面积为 0 的图元误判。
    ent_len = max(
        ent_max_x - ent_min_x,
        ent_max_y - ent_min_y,
    )
    if ent_len <= 0:
        # 点状图元：能走到这里说明点落在矩形内
        return 1.0
    in_len = max(ix2 - ix1, iy2 - iy1)
    return in_len / ent_len


# 归集判定所需的最小覆盖比例。
# 视口/图框只应保留取景框内的内容：穿框图形（如跨越相邻视口的承台块、
# 穿过图框的长线）覆盖比例通常仅 0.5 左右，而框内正常实体几乎全部
# >=0.99。取 0.9 既能去掉穿框图形，又能容许正常实体的边沿浮点误差。
COVERAGE_THRESHOLD = 0.9

# 不参与裁剪的图框类型：fallback 整图本身就是图纸的载体，
# 整张图纸都在框内，不存在"超出框"的概念。
NOT_CLIPPED_KINDS = frozenset({"fallback"})


def _frame_bounds(frame: Frame):
    """根据 source_kind 返回 (矩形, 是否裁剪) 二元组。

    对 viewport 类型：返回 frame 自身的图纸空间矩形（裁剪窗口）。
    模型空间查找窗口由 viewport_ms_bounds 单独承担，不在此返回。
    """
    if frame.source_kind == "viewport":
        return (
            (frame.min_x, frame.min_y, frame.max_x, frame.max_y),
            True,
        )
    if frame.source_kind == "fallback":
        # fallback 帧矩形即整图几何范围；clip=False 表示只判相交、
        # 不做覆盖率裁剪（见 _entity_in_frame_by_coverage）。
        return (
            (frame.min_x, frame.min_y, frame.max_x, frame.max_y),
            False,
        )
    # polyline / block：图框矩形即 (min_x, min_y, max_x, max_y)
    return (
        (frame.min_x, frame.min_y, frame.max_x, frame.max_y),
        True,
    )


def _entity_in_ms_bounds(entity, frame: Frame) -> bool:
    """判断模型空间实体是否"主要"位于 viewport 在模型空间的覆盖矩形内。

    与 _entity_in_frame_by_coverage 的区别：归属窗口是 frame.viewport_ms_bounds
    （模型空间坐标），而不是 frame 的图纸空间矩形。用于图纸空间布局下
    把视口显示的内容归集到对应图框。
    """
    ms_rect = getattr(frame, "viewport_ms_bounds", None)
    if ms_rect is None:
        return False
    ent_box = _entity_bbox(entity)
    if ent_box is None:
        anchor = _entity_anchor(entity)
        if anchor is None:
            return False
        return _point_in_rect(anchor[0], anchor[1], ms_rect)
    fx1, fy1, fx2, fy2 = ms_rect
    if (
        ent_box[2] <= fx1 or ent_box[0] >= fx2
        or ent_box[3] <= fy1 or ent_box[1] >= fy2
    ):
        return False
    cov = _coverage_in_frame(*ent_box, ms_rect)
    return cov >= COVERAGE_THRESHOLD


def _entity_in_frame_by_coverage(entity, frame: Frame) -> bool:
    """判断图元是否"主要"位于该图框矩形内。

    bbox 求不出（视口图元、TEXT 等退化情况）→ 仅以归属点判定，
    行为兼容原逻辑。
    """
    ent_box = _entity_bbox(entity)
    if ent_box is None:
        anchor = _entity_anchor(entity)
        if anchor is None:
            return False
        return _point_in_frame(anchor[0], anchor[1], frame)

    frame_box, clip = _frame_bounds(frame)
    if frame_box is None:
        return False
    if not clip:
        # fallback 不裁剪：只要 bbox 与 frame 相交就算属于
        fx1, fy1, fx2, fy2 = frame_box
        if (
            ent_box[2] < fx1 or ent_box[0] > fx2
            or ent_box[3] < fy1 or ent_box[1] > fy2
        ):
            return False
        return True

    fx1, fy1, fx2, fy2 = frame_box
    # 完全在外：快速拒绝
    if (
        ent_box[2] <= fx1 or ent_box[0] >= fx2
        or ent_box[3] <= fy1 or ent_box[1] >= fy2
    ):
        return False
    cov = _coverage_in_frame(*ent_box, frame_box)
    return cov >= COVERAGE_THRESHOLD


def _entity_frame_coverage(entity, frame: Frame) -> float:
    """返回图元在该图框内的「覆盖度」分数（越高越贴合）。

    与布尔版 _entity_in_frame_by_coverage 共用判定，但返回量化分数，
    用于在多个重叠图框间为图元挑选最贴合的归属帧（如标题块同时穿过
    一个视口帧和它自身的 block 帧：在视口内覆盖 ~58%，在自身块帧内
    覆盖 100%，应归自身块帧，而非排序靠前、首个命中的视口帧）。

    bbox 求不出（视口图元、TEXT 等退化情况）→ 锚点命中给满分 1.0，
    未命中给 0.0。
    """
    ent_box = _entity_bbox(entity)
    if ent_box is None:
        anchor = _entity_anchor(entity)
        if anchor is None:
            return 0.0
        return 1.0 if _point_in_frame(anchor[0], anchor[1], frame) else 0.0

    frame_box, clip = _frame_bounds(frame)
    if frame_box is None:
        return 0.0
    fx1, fy1, fx2, fy2 = frame_box
    if not clip:
        # fallback 不裁剪：bbox 相交即属于
        if (
            ent_box[2] < fx1 or ent_box[0] > fx2
            or ent_box[3] < fy1 or ent_box[1] > fy2
        ):
            return 0.0
        return 1.0
    # 完全在外：快速拒绝
    if (
        ent_box[2] <= fx1 or ent_box[0] >= fx2
        or ent_box[3] <= fy1 or ent_box[1] >= fy2
    ):
        return 0.0
    return _coverage_in_frame(*ent_box, frame_box)


def _assign_entities_in_layout(
    layout, frames: List[Frame], doc=None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> List[Set[Any]]:
    """遍历单个布局，把每个图元归到归属点所在的图框。

    优化点（对比 v1）：
    - 把"对每个图元 O(N) 遍历图框"改成"对每个图元只扫一遍图框，
      但在第一帧命中后即 break"——已经满足；进一步：把每组图框按
      「是否需要 coverage 计算」预分类，简单点 in_frame 直接走
      包围盒四向比较即可，不再每次都算 coverage_in_frame。
    - viewport_ms_bounds 用预提取的元组缓存，避免每实体一行 getattr。

    frames 必须全部属于该布局。返回与 frames 等长的列表，每项为
    属于该图框的图元集合；一个图元只归入首个命中的图框，不属于
    任何图框则丢弃。

    :param on_progress: 可选回调，签名 (current, total)，每隔 ~256 个
        图元调用一次，用于把"扫描大布局"期间的进度平滑推给前端。
    """
    buckets: List[Set[Any]] = [set() for _ in frames]
    is_paperspace = not getattr(layout, "is_modelspace", False)
    has_viewport = any(getattr(f, "viewport_ms_bounds", None) for f in frames)

    # 1) 本布局直接图元（始终执行）
    total = len(layout)
    last_reported = 0
    for idx, entity in enumerate(layout):
        try:
            # 每个图元的覆盖度对各帧只差矩形参数，bbox 只需算一次，
            # 避免大图纸（4.7 万图元 × 37 帧）时重复计算 37 遍。
            ent_box = _entity_bbox(entity)
            anchor = None
            if ent_box is None:
                anchor = _entity_anchor(entity)
            best_i = -1
            best_cov = COVERAGE_THRESHOLD
            for i, frame in enumerate(frames):
                if ent_box is not None:
                    frame_box, clip = _frame_bounds(frame)
                    if clip:
                        if (
                            ent_box[2] <= frame_box[0]
                            or ent_box[0] >= frame_box[2]
                            or ent_box[3] <= frame_box[1]
                            or ent_box[1] >= frame_box[3]
                        ):
                            cov = 0.0
                        else:
                            cov = _coverage_in_frame(*ent_box, frame_box)
                    else:
                        if (
                            ent_box[2] < frame_box[0]
                            or ent_box[0] > frame_box[2]
                            or ent_box[3] < frame_box[1]
                            or ent_box[1] > frame_box[3]
                        ):
                            cov = 0.0
                        else:
                            cov = 1.0
                else:
                    if anchor is None:
                        cov = 0.0
                    else:
                        cov = (
                            1.0
                            if _point_in_frame(anchor[0], anchor[1], frame)
                            else 0.0
                        )
                if cov >= best_cov:
                    best_cov = cov
                    best_i = i
            if best_i >= 0:
                buckets[best_i].add(entity)
        except Exception:
            continue
        if on_progress is not None and (idx - last_reported) >= 256:
            last_reported = idx
            try:
                on_progress(idx, total)
            except Exception:
                pass
    if on_progress is not None:
        try:
            on_progress(total, total)
        except Exception:
            pass

    # 2) 图纸空间里视口引用的模型空间图元
    if is_paperspace and has_viewport and doc is not None:
        # 每个帧按其实际视口展开成多条 ms 矩形：标题块帧含多个视口时
        # 分别判定，避免按 union 大矩形把落在视口间隙里的实体也并入。
        ms_bounds_cache: List[tuple] = []
        for i, f in enumerate(frames):
            contained = getattr(f, "contained_viewports", None)
            if contained:
                for ms, _vp in contained:
                    if ms is not None:
                        ms_bounds_cache.append(
                            (i, ms, ms[0], ms[1], ms[2], ms[3])
                        )
            elif getattr(f, "viewport_ms_bounds", None) is not None:
                ms = f.viewport_ms_bounds
                ms_bounds_cache.append(
                    (i, ms, ms[0], ms[1], ms[2], ms[3])
                )
        for entity in doc.modelspace():
            try:
                ent_box = _entity_bbox(entity)
                anchor = None
                if ent_box is None:
                    anchor = _entity_anchor(entity)
                    if anchor is None:
                        continue
                best_i = -1
                best_cov = COVERAGE_THRESHOLD
                for i, _ms, fx1, fy1, fx2, fy2 in ms_bounds_cache:
                    if ent_box is not None:
                        if (
                            ent_box[2] <= fx1 or ent_box[0] >= fx2
                            or ent_box[3] <= fy1 or ent_box[1] >= fy2
                        ):
                            continue
                        cov = _coverage_in_frame(*ent_box, (fx1, fy1, fx2, fy2))
                    else:
                        cov = (
                            1.0
                            if (
                                fx1 - 1e-6 <= anchor[0] <= fx2 + 1e-6
                                and fy1 - 1e-6 <= anchor[1] <= fy2 + 1e-6
                            )
                            else 0.0
                        )
                    if cov > best_cov:
                        best_cov = cov
                        best_i = i
                if best_i >= 0:
                    buckets[best_i].add(entity)
            except Exception:
                continue

    return buckets


def _new_document(src_doc):
    """创建新 DXF 文档，版本不低于 R2004（AC1018，不支持时回退 R2010）。

    统一输出 UTF-8：DXF R2004 起标准编码即为 UTF-8，dxf-viewer 默认也按
    UTF-8 解码。LibreDWG 转出的旧版图纸中文为 GBK（ANSI_936），若沿用
    源编码，前端会把 GBK 字节误读成替换字符，故此处不继承源代码页。
    ezdxf 内部以 Unicode 保存实体文本，写入 UTF-8 文档时可正常编码中文。
    """
    version = src_doc.dxfversion
    if version < "AC1018":
        version = "AC1018"
    try:
        new_doc = ezdxf.new(version)
    except Exception:
        new_doc = ezdxf.new("R2010")
    # ezdxf 默认按 ANSI_1252 写盘；显式指定 UTF-8，中文实体文本才能正确编码
    new_doc.encoding = "utf-8"
    return new_doc


def _matrix_for_entity(entity, vp_list):
    """为模型来源克隆挑选所属视口的模型→图纸变换矩阵。

    :param vp_list: [(ms 覆盖矩形, VIEWPORT 图元), ...]
    按克隆在模型空间的包围盒对各视口区域的覆盖率取最高者；求不出
    包围盒时退回锚点包含判定；都失败时用第一个视口兜底，保证模型
    内容一定会被搬到图纸坐标附近。
    """
    ent_box = _entity_bbox(entity)
    best_vp = None
    best_cov = 0.0
    if ent_box is not None:
        for ms_rect, vp in vp_list:
            if (
                ent_box[2] <= ms_rect[0] or ent_box[0] >= ms_rect[2]
                or ent_box[3] <= ms_rect[1] or ent_box[1] >= ms_rect[3]
            ):
                continue
            cov = _coverage_in_frame(*ent_box, ms_rect)
            if cov > best_cov:
                best_cov = cov
                best_vp = vp
    else:
        anchor = _entity_anchor(entity)
        if anchor is not None:
            for ms_rect, vp in vp_list:
                if _point_in_rect(anchor[0], anchor[1], ms_rect):
                    best_vp = vp
                    break
    if best_vp is None:
        best_vp = vp_list[0][1]
    return viewport_transform_matrix(best_vp)


def split_document(
    doc,
    frames: List[Frame],
    out_dir: str,
    on_sheet: Optional[Callable[[int, int], None]] = None,
    on_assign_progress: Optional[Callable[[int, int], None]] = None,
) -> List[dict]:
    """为每个图框生成独立 DXF，并写出 manifest.json。

    图框按所属布局分组：模型空间图框用 xref.load_modelspace 导入，
    图纸空间图框用 Loader 把命中的模型空间实体（视口显示的内容）与
    命中的图纸空间图元（视口边框、标题栏）一起导入目标文档的模型空间
    （dxf-viewer 只渲染模型空间）。

    :param on_sheet: 进度回调，签名 (i, total)，在每张图纸 saveas 之后
        调用一次。
    :param on_assign_progress: 进度回调，签名 (current, total)，
        在「按布局遍历图元归集」过程中定期调用，用来把大布局扫描的
        中间进度实时推给前端。
    """
    os.makedirs(out_dir, exist_ok=True)
    sheets: List[dict] = []
    sheet_no = 0

    # frames 已按 detect_frames 的布局顺序排列，按布局连续分组
    layout_frames: List[tuple] = []
    for frame in frames:
        if layout_frames and layout_frames[-1][0] == frame.layout_name:
            layout_frames[-1][1].append(frame)
        else:
            layout_frames.append((frame.layout_name, [frame]))

    total_sheets = len(frames)
    for layout_name, frames_in_layout in layout_frames:
        layout = doc.layouts.get(layout_name)
        buckets = _assign_entities_in_layout(
            layout, frames_in_layout, doc,
            on_progress=on_assign_progress,
        )

        for frame, owned in zip(frames_in_layout, buckets):
            sheet_no += 1
            sheet_id = "sheet_%d" % sheet_no
            new_doc = _new_document(doc)

            if getattr(layout, "is_modelspace", False):
                # 模型空间：直接按过滤条件导入
                xref.load_modelspace(
                    doc,
                    new_doc,
                    filter_fn=lambda entity, _owned=owned: entity in _owned,
                    conflict_policy=xref.ConflictPolicy.KEEP,
                )
            else:
                # 图纸空间：
                #   1) 把命中的模型空间实体（视口显示的内容，文件实存于
                #      doc.modelspace()）导入目标模型空间；
                #   2) 再把命中的图纸空间图元（视口边框、标题栏）导入目标
                #      模型空间。
                # 两个 load 复用同一个 Loader，execute() 一次性提交。
                loader = xref.Loader(
                    doc, new_doc,
                    conflict_policy=xref.ConflictPolicy.KEEP,
                )
                loader.load_modelspace(
                    filter_fn=lambda entity, _owned=owned: entity in _owned,
                )
                loader.load_paperspace_layout_into(
                    layout,
                    new_doc.modelspace(),
                    filter_fn=lambda entity, _owned=owned: entity in _owned,
                )
                loader.execute()

                # dxf-viewer 只按平面坐标渲染、不识别视口，而上面导入的
                # 模型空间实体仍保持模型坐标（与图纸坐标相差数百万单位），
                # 与图纸空间图元混放后 FitView 会把真正内容压成一个小点。
                # 用视口的模型→图纸变换矩阵把模型来源克隆搬到图纸坐标；
                # 图纸空间原生克隆（视口、标题栏）不动。
                vp_entity = getattr(frame, "source_entity", None)
                # 标题块帧内含多个视口时，每个模型图元按其所在的模型
                # 区域匹配具体视口，使用该视口自己的变换矩阵；
                # 纯视口帧只有一个矩阵。
                contained = getattr(frame, "contained_viewports", None)
                if contained is not None and contained:
                    vp_list = contained
                elif vp_entity is not None and hasattr(
                    vp_entity, "get_transformation_matrix"
                ):
                    vp_list = [(frame.viewport_ms_bounds, vp_entity)]
                else:
                    vp_list = []
                if vp_list:
                    # xref 复制会因 handle 冲突给克隆重新编号，无法用
                    # handle 对应源实体。这里用「(类型, 包围盒中心, bbox
                    # 尺寸)」签名识别模型来源克隆：几何尚未变换，克隆
                    # 与源同坐标。
                    ms_key = doc.modelspace().layout_key
                    # 用计数多重集合而非 set：图纸里存在同几何签名的实体
                    # （如重复的块/文字），set + discard 会让除第一个外的
                    # 同名克隆漏变换，坐标仍停在模型空间。
                    from collections import Counter
                    ms_sigs = Counter(
                        _entity_signature(e)
                        for e in owned
                        if e.dxf.owner == ms_key
                    )
                    for entity in new_doc.modelspace():
                        sig = _entity_signature(entity)
                        if ms_sigs.get(sig, 0) <= 0:
                            continue
                        ms_sigs[sig] -= 1
                        matrix = _matrix_for_entity(entity, vp_list)
                        if matrix is None:
                            continue
                        try:
                            entity.transform(matrix)
                        except Exception:
                            continue

            path = os.path.join(out_dir, "%s.dxf" % sheet_id)
            new_doc.saveas(path)

            sheets.append(
                {
                    "id": sheet_id,
                    "name": frame.name,
                    "layout": frame.layout_name,
                    "width": round(frame.width, 1),
                    "height": round(frame.height, 1),
                    "frame": {
                        "min_x": frame.min_x,
                        "min_y": frame.min_y,
                        "max_x": frame.max_x,
                        "max_y": frame.max_y,
                    },
                }
            )

            if on_sheet:
                try:
                    on_sheet(sheet_no - 1, total_sheets)
                except Exception:
                    # 回调异常不应阻断拆分
                    pass

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fp:
        json.dump({"sheets": sheets}, fp, ensure_ascii=False, indent=2)

    return sheets