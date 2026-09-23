# -*- coding: utf-8 -*-
"""Task 4 demo：用 mock provider 验证 llm_infer 完整链路。

用法：
    cd backend && python3 -m app.audit.demo_llm
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import ezdxf


def _build_sheets(sheet_dir: str) -> None:
    """构造两张 sheet：钻孔灌注桩布置图 + 承台详图。"""
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    for i in range(24):
        cx, cy = (i % 6) * 1000, (i // 6) * 1000
        msp.add_circle((cx, cy), radius=400)
    msp.add_text("钻孔灌注桩 N=24 Φ800 长度 L=18m 体积 V=180", dxfattribs={"height": 100, "insert": (0, -500)})
    doc.saveas(os.path.join(sheet_dir, "sheet_1.dxf"))

    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (4000, 0), (4000, 1500), (0, 1500)], close=True)
    msp.add_text("承台 CT-3 数量 N=6 厚度 h=1.2m 体积 V=28.8", dxfattribs={"height": 100, "insert": (0, -300)})
    doc.saveas(os.path.join(sheet_dir, "sheet_2.dxf"))


class _MockProvider:
    name = "mock"
    protocol = "openai"

    def __init__(self):
        self.config = {"id": "mock", "chatModel": "mock-chat"}

    @property
    def chat_model(self):
        return "mock-chat"

    async def chat(self, messages, *, model=None, temperature=0.2):
        prompt = messages[-1]["content"] if messages else ""
        if "sheet_2" in prompt:
            return json.dumps({
                "byCategory": {
                    "cap": [
                        {
                            "id": "sheet_2:cap",
                            "sheetIds": ["sheet_2"],
                            "fields": {
                                "code": {"value": "CT-3", "trace": "annotation|直接命中「承台 CT-3」"},
                                "count": {"value": 6, "trace": "annotation|标注 N=6"},
                                "shape": {"value": "矩形", "trace": "annotation|从 LWPOLYLINE 矩形判断"},
                                "area": {"value": 6.0, "trace": "geometry|4m×1.5m"},
                                "thickness": {"value": 1.2, "trace": "annotation|标注 h=1.2m"},
                                "volume": {"value": 28.8, "trace": "llm|矩形 6 个合计 4×1.5×1.2×6=28.8"},
                            },
                        }
                    ],
                }
            }, ensure_ascii=False)
        # 默认 sheet_1
        return json.dumps({
            "byCategory": {
                "bored_pile": [
                    {
                        "id": "sheet_1:bored_pile",
                        "sheetIds": ["sheet_1"],
                        "fields": {
                            "type": {"value": "钻孔灌注", "trace": "annotation|直接命中关键字"},
                            "count": {"value": 24, "trace": "annotation|标注 N=24"},
                            "diameter": {"value": 800, "trace": "annotation|命中 Φ800"},
                            "area": {"value": 0.5027, "trace": "geometry|π×0.4²≈0.5027m²"},
                            "volume": {"value": 180.0, "trace": "annotation|标注 V=180"},
                        },
                    }
                ],
            }
        }, ensure_ascii=False)


class _BrokenProvider(_MockProvider):
    async def chat(self, messages, *, model=None, temperature=0.2):
        # 给一个非 JSON 的输出，触发解析失败兜底
        return "哎呀模型挂了，输出一段乱码：" + "x" * 60


async def main():
    print("=== Task 4 demo: llm_infer ===")
    with tempfile.TemporaryDirectory() as tmp:
        sheet_dir = os.path.join(tmp, "sheets")
        os.makedirs(sheet_dir, exist_ok=True)
        _build_sheets(sheet_dir)
        manifest = {
            "sheets": [
                {"id": "sheet_1", "name": "钻孔灌注桩布置图"},
                {"id": "sheet_2", "name": "承台 CT-3"},
            ]
        }
        with open(os.path.join(sheet_dir, "manifest.json"), "w", encoding="utf-8") as fp:
            json.dump(manifest, fp, ensure_ascii=False)

        # 跑规则管线作为 llm_infer 的输入
        from app.audit import run_rules_pipeline, run_llm_infer, rule_draft_to_json
        rule_draft = run_rules_pipeline(sheet_dir)
        print("\n[1] rule_draft (阶段① inputs):")
        print(json.dumps(rule_draft_to_json(rule_draft), ensure_ascii=False, indent=2)[:1500])

        sheet_ids = ["sheet_1", "sheet_2"]
        relevant_layers = {"sheet_1": ["钻孔桩"], "sheet_2": ["承台"]}

        # ---- (a) mock 正常 ----
        print("\n[2] run_llm_infer (mock provider, 正常):")
        progress = []
        result = await run_llm_infer(
            _MockProvider(), sheet_dir, sheet_ids, rule_draft,
            relevant_layers_by_sheet=relevant_layers,
            on_progress=lambda i, total, msg: progress.append((i, total, msg)),
        )
        print("    progress:", progress)
        print("\n[3] LlmResult:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # ---- (b) mock 异常 ----
        print("\n[4] run_llm_infer (broken provider, 失败兜底):")
        result2 = await run_llm_infer(
            _BrokenProvider(), sheet_dir, sheet_ids, rule_draft,
            relevant_layers_by_sheet=relevant_layers,
        )
        print(json.dumps(result2, ensure_ascii=False, indent=2))


def _run():
    asyncio.run(main())


if __name__ == "__main__":
    _run()