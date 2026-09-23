# -*- coding: utf-8 -*-
"""把单张 sheet DXF 渲染为 PNG（用于 VLM 视觉输入）。

设计要点：
    - 使用 ezdxf.addons.drawing + MatplotlibBackend（ezdxf 官方推荐的
      matplotlib 渲染路径），避免引入额外渲染依赖；
    - 走 layout.bounds() 自动取模型空间范围，按 mm/pixel 比例等比缩放，
      默认 3.0 px/mm，长边上限 4096 px（控制 token 数）；
    - 渲染失败时返回 None，由调用方走"跳过该 sheet"路径。

依赖：
    - matplotlib >= 3.5
    - ezdxf.addons.drawing（ezdxf 自带）
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend


log = logging.getLogger(__name__)


# 默认参数：3 px/mm（A0 长边 1189mm → 3567 px，离 4096 上限有裕量）
DEFAULT_PX_PER_MM = 3.0
MAX_LONG_EDGE_PX = 4096


def _resize_for_cap(px_per_mm: float, long_mm: float) -> float:
    """若按当前 px/mm 渲染后长边超过 MAX_LONG_EDGE_PX，则等比缩小比例。"""
    long_px = px_per_mm * long_mm
    if long_px <= MAX_LONG_EDGE_PX:
        return px_per_mm
    return MAX_LONG_EDGE_PX / max(1.0, long_mm)


def render_dxf_to_png(
    dxf_path: str,
    *,
    px_per_mm: float = DEFAULT_PX_PER_MM,
    dpi: int = 96,
) -> Optional[bytes]:
    """把 DXF 文件的模型空间画成 PNG，返回 PNG 字节；失败返回 None。

    :param dxf_path: 输入 DXF 路径
    :param px_per_mm: 1 mm 对应多少像素；超出长边上限会自动等比缩小
    :param dpi: matplotlib DPI；同时影响字号/线宽
    """
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as exc:
        log.warning("render: readfile failed %s: %s", dxf_path, exc)
        return None
    try:
        msp = doc.modelspace()
        if len(msp) == 0:
            # 空图：返回 1x1 透明 PNG，避免上游 None 处理复杂度
            return _empty_png()
        # 计算包围盒：Modelspace 自身没有 .bounds()，统一用 ezdxf.bbox.extents
        try:
            extents = ezdxf.bbox.extents(msp, fast=True)
        except Exception:
            extents = ezdxf.bbox.extents(msp)
        if not extents.has_data:
            return _empty_png()
        min_x, min_y, max_x, max_y = extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y
        long_mm = max(max_x - min_x, max_y - min_y)
        if long_mm <= 0:
            return _empty_png()

        scale = _resize_for_cap(px_per_mm, long_mm)
        # 在 96 dpi 下，1 mm ≈ 96/25.4 ≈ 3.78 像素；这里用 scale
        # 表示「宽度×多少 mm」→ 留 4mm 白边
        width_mm = (max_x - min_x) + 8.0
        height_mm = (max_y - min_y) + 8.0
        width_in = width_mm * scale / dpi
        height_in = height_mm * scale / dpi

        # 偏移：把图放到右下角的边距内
        offset_x = -min_x + 4.0
        offset_y = -min_y + 4.0

        fig_w = max(0.5, width_in)
        fig_h = max(0.5, height_in)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_axis_off()
        ax.set_xlim(0, width_mm)
        ax.set_ylim(0, height_mm)
        ax.set_aspect("equal")
        # 白底
        ax.set_facecolor("white")

        ctx = RenderContext(doc)
        ctx.set_current_layout(msp)
        backend = MatplotlibBackend(ax)
        frontend = Frontend(ctx, backend)
        # 包围盒外的实体也要画上：把当前布局设为 msp，draw_layout 即可
        from ezdxf.addons.drawing.properties import LayoutProperties
        frontend.draw_layout(
            msp,
            layout_properties=LayoutProperties.from_layout(msp),
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:
        log.warning("render: drawing failed %s: %s", dxf_path, exc)
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            pass
        return None


def _empty_png() -> bytes:
    """返回一张 1x1 透明 PNG，作为「空图」兜底。"""
    import base64
    # 1x1 透明 PNG base64（最短）
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


def render_sheet_to_cache(
    sheet_dir: str,
    sheet_id: str,
    *,
    force: bool = False,
) -> Optional[str]:
    """把 sheet_dir/{sheet_id}.dxf 渲染并写入 sheet_dir/{sheet_id}.png。

    命中（PNG 已存在且 mtime 较新）时直接返回路径。失败返回 None。

    返回 PNG 的绝对路径，供后续 VLM 调用读取。
    """
    dxf_path = os.path.join(sheet_dir, f"{sheet_id}.dxf")
    png_path = os.path.join(sheet_dir, f"{sheet_id}.png")
    if (
        not force
        and os.path.isfile(png_path)
        and os.path.isfile(dxf_path)
        and os.path.getmtime(png_path) >= os.path.getmtime(dxf_path)
    ):
        return png_path
    data = render_dxf_to_png(dxf_path)
    if data is None:
        return None
    try:
        with open(png_path, "wb") as fp:
            fp.write(data)
        return png_path
    except Exception as exc:
        log.warning("render: write png failed %s: %s", png_path, exc)
        return None


__all__ = [
    "render_dxf_to_png",
    "render_sheet_to_cache",
    "DEFAULT_PX_PER_MM",
    "MAX_LONG_EDGE_PX",
]