# -*- coding: utf-8 -*-
"""Task 3 demo：手动跑 render + vlm_select 完整链路（mock provider，不打外网）。

用法：
    cd backend && python3 -m app.audit.demo_vlm
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import ezdxf


def _build_sheets(sheet_dir: str) -> None:
    """构造 4 张演示 sheet：围护桩平面 / 钻孔灌注桩剖面 / 承台详图 / 图纸目录。"""
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()

    # ---- sheet_1：围护桩平面图（带大量圆形 + 文字）----
    for i in range(30):
        cx, cy = (i % 6) * 1000, (i // 6) * 1000
        msp.add_circle((cx, cy), radius=450)
    msp.add_text("围护桩 平面布置图 N=30 Φ900 L=20m", dxfattribs={"height": 200, "insert": (0, 6500)})
    msp.add_text("图层: 围护桩-A1, 结构-钢筋", dxfattribs={"height": 100, "insert": (0, -500)})
    doc.saveas(os.path.join(sheet_dir, "sheet_1.dxf"))

    # ---- sheet_2：钻孔灌注桩剖面图 ----
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (6000, 0), (6000, 3000), (0, 3000)], close=True)
    msp.add_circle((1500, 0), radius=400)
    msp.add_circle((4500, 0), radius=400)
    msp.add_text("钻孔灌注桩 剖面图 N=2 Φ800 L=18m", dxfattribs={"height": 200, "insert": (0, 3500)})
    doc.saveas(os.path.join(sheet_dir, "sheet_2.dxf"))

    # ---- sheet_3：承台详图 ----
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (4000, 0), (4000, 1500), (0, 1500)], close=True)
    msp.add_text("承台 CT-3 数量 N=6 厚度 h=1.2m", dxfattribs={"height": 200, "insert": (0, -500)})
    doc.saveas(os.path.join(sheet_dir, "sheet_3.dxf"))

    # ---- sheet_4：图纸目录（与工程量无关）----
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    for i, line in enumerate([
        "图纸目录", "01 总平面图", "02 围护桩平面图", "03 承台详图",
        "04 节点大样图", "05 设计说明", "06 工程数量表",
    ]):
        msp.add_text(line, dxfattribs={"height": 200, "insert": (1000, 6000 - i * 700)})
    doc.saveas(os.path.join(sheet_dir, "sheet_4.dxf"))


class _MockProvider:
    """不依赖网络的 mock provider，按规则产出合法 JSON。"""

    name = "mock"
    protocol = "openai"

    def __init__(self):
        self.config = {"id": "mock", "chatModel": "mock-chat", "visionModel": "mock-vision"}

    @property
    def chat_model(self):
        return "mock-chat"

    @property
    def vision_model(self):
        return "mock-vision"

    async def vision(self, images, prompt):
        # 从 prompt 里抓 sheetIds
        import re
        m = re.search(r"sheetIds 列表[^\n]*\n(\[[^\]]+\])", prompt)
        ids = json.loads(m.group(1)) if m else []
        out = []
        for sid in ids:
            if sid == "sheet_1":
                out.append({"sheetId": sid, "score": 0.92, "role": "plan",
                            "reason": "围护桩平面图，含桩位+数量表", "relevantLayers": ["围护桩-A1", "结构-钢筋"]})
            elif sid == "sheet_2":
                out.append({"sheetId": sid, "score": 0.81, "role": "section",
                            "reason": "钻孔灌注桩剖面，含桩径/数量", "relevantLayers": ["灌注桩", "剖面-标注"]})
            elif sid == "sheet_3":
                out.append({"sheetId": sid, "score": 0.78, "role": "detail",
                            "reason": "承台详图，含 CT-3 编号+厚度", "relevantLayers": ["承台", "结构-钢筋"]})
            elif sid == "sheet_4":
                out.append({"sheetId": sid, "score": 0.15, "role": "title",
                            "reason": "图纸目录，不含工程量", "relevantLayers": []})
            else:
                out.append({"sheetId": sid, "score": 0.3, "role": "other",
                            "reason": "未识别", "relevantLayers": []})
        return json.dumps(out, ensure_ascii=False)


def main():
    print("=== Task 3 demo: render + vlm_select ===")
    with tempfile.TemporaryDirectory() as tmp:
        sheet_dir = os.path.join(tmp, "sheets")
        os.makedirs(sheet_dir, exist_ok=True)
        _build_sheets(sheet_dir)
        manifest = {
            "sheets": [
                {"id": "sheet_1", "name": "围护桩平面布置图"},
                {"id": "sheet_2", "name": "钻孔灌注桩剖面图"},
                {"id": "sheet_3", "name": "承台 CT-3 详图"},
                {"id": "sheet_4", "name": "图纸目录"},
            ]
        }
        with open(os.path.join(sheet_dir, "manifest.json"), "w", encoding="utf-8") as fp:
            json.dump(manifest, fp, ensure_ascii=False)

        # 1) 单图渲染
        print("\n[1] render_dxf_to_png(sheet_1.dxf):")
        png = __import__("app.audit.render", fromlist=["render_dxf_to_png"]).render_dxf_to_png(
            os.path.join(sheet_dir, "sheet_1.dxf")
        )
        print("    PNG bytes:", len(png) if png else None)
        # 写文件验证 cache
        cached = __import__("app.audit.render", fromlist=["render_sheet_to_cache"]).render_sheet_to_cache(sheet_dir, "sheet_1")
        print("    cached path:", cached, "exists=", os.path.isfile(cached) if cached else None)

        # 2) vlm_select（mock provider）
        print("\n[2] run_vlm_selection(mock provider):")
        from app.audit import run_vlm_selection
        provider = _MockProvider()
        sheet_ids = ["sheet_1", "sheet_2", "sheet_3", "sheet_4"]
        progress = []
        results = asyncio.run(run_vlm_selection(
            provider, sheet_dir, sheet_ids,
            on_progress=lambda i, total, msg: progress.append((i, total, msg)),
        ))
        print("    progress:", progress)
        print("\n[3] VlmCandidates:")
        print(json.dumps(results, ensure_ascii=False, indent=2))

        # 3) 失败兜底（用 _MockBrokenProvider 模拟网络/解析失败）
        print("\n[4] 模拟 vision 失败（broken provider）：")

        class _BrokenProvider(_MockProvider):
            async def vision(self, images, prompt):
                raise RuntimeError("假装网络挂了")

        results2 = asyncio.run(run_vlm_selection(_BrokenProvider(), sheet_dir, sheet_ids))
        print(json.dumps(results2, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()