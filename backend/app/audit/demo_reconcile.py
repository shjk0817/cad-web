# -*- coding: utf-8 -*-
"""Task 5 demo：手动跑 rules + llm_infer + reconcile 三阶段验证。

用法：
    cd backend && python3 -m app.audit.demo_reconcile
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import ezdxf


def _build_sheets(sheet_dir: str) -> None:
    """构造两张 sheet：钻孔灌注桩（圆形密 + 标注）+ 承台（矩形 + 标注）。"""
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

    def __init__(self, *, mismatched_volume: bool = False):
        self.mismatched_volume = mismatched_volume
        self.config = {"id": "mock", "chatModel": "mock-chat"}

    @property
    def chat_model(self):
        return "mock-chat"

    async def chat(self, messages, *, model=None, temperature=0.2):
        prompt = messages[-1]["content"] if messages else ""
        if "sheet_2" in prompt and "sheet_1" in prompt:
            # 同时含 sheet_1+2：bored_pile 来自 sheet_1，cap 来自 sheet_2
            return json.dumps({
                "byCategory": {
                    "bored_pile": [
                        {
                            "id": "sheet_1:bored_pile",
                            "sheetIds": ["sheet_1"],
                            "fields": {
                                "type": {"value": "钻孔灌注", "trace": "annotation"},
                                "count": {"value": 24, "trace": "annotation|N=24"},
                                "diameter": {"value": 800, "trace": "annotation|Φ800"},
                                "area": {"value": 0.5027, "trace": "geometry|πr²"},
                                "volume": {"value": (999.9 if self.mismatched_volume else 180.0), "trace": "annotation|V=180（llm 误判）" if self.mismatched_volume else "annotation|V=180"},
                            },
                        }
                    ],
                    "cap": [
                        {
                            "id": "sheet_2:cap",
                            "sheetIds": ["sheet_2"],
                            "fields": {
                                "code": {"value": "CT-3", "trace": "annotation|直接命中"},
                                "count": {"value": 6, "trace": "annotation|N=6"},
                                "shape": {"value": "矩形", "trace": "annotation|LWPOLYLINE 矩形"},
                                "area": {"value": 6.0, "trace": "geometry|4m×1.5m"},
                                "thickness": {"value": 1.2, "trace": "annotation|h=1.2m"},
                                "volume": {"value": 28.8, "trace": "annotation|V=28.8"},
                            },
                        }
                    ],
                }
            }, ensure_ascii=False)
        if "sheet_2" in prompt:
            return json.dumps({
                "byCategory": {
                    "cap": [
                        {
                            "id": "sheet_2:cap",
                            "sheetIds": ["sheet_2"],
                            "fields": {
                                "code": {"value": "CT-3", "trace": "annotation|直接命中"},
                                "count": {"value": 6, "trace": "annotation|N=6"},
                                "shape": {"value": "矩形", "trace": "annotation|LWPOLYLINE 矩形"},
                                "area": {"value": 6.0, "trace": "geometry|4m×1.5m"},
                                "thickness": {"value": 1.2, "trace": "annotation|h=1.2m"},
                                "volume": {"value": 28.8, "trace": "annotation|V=28.8"},
                            },
                        }
                    ],
                }
            }, ensure_ascii=False)
        # 默认 sheet_1
        vol = 999.9 if self.mismatched_volume else 180.0
        return json.dumps({
            "byCategory": {
                "bored_pile": [
                    {
                        "id": "sheet_1:bored_pile",
                        "sheetIds": ["sheet_1"],
                        "fields": {
                            "type": {"value": "钻孔灌注", "trace": "annotation"},
                            "count": {"value": 24, "trace": "annotation|N=24"},
                            "diameter": {"value": 800, "trace": "annotation|Φ800"},
                            "area": {"value": 0.5027, "trace": "geometry|πr²"},
                            "volume": {"value": vol, "trace": "annotation|V=180（llm 误判）" if self.mismatched_volume else "annotation|V=180"},
                        },
                    }
                ],
            }
        }, ensure_ascii=False)


async def main():
    print("=== Task 5 demo: rules + llm_infer + reconcile ===")
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

        from app.audit import (
            run_rules_pipeline, rule_draft_to_json,
            run_llm_infer, run_reconcile,
        )

        rule_draft = run_rules_pipeline(sheet_dir)
        print("\n[1] rule_draft summary:")
        print(json.dumps(rule_draft_to_json(rule_draft), ensure_ascii=False, indent=2)[:1500])

        sheet_ids = ["sheet_1", "sheet_2"]
        relevant = {"sheet_1": ["钻孔桩"], "sheet_2": ["承台"]}

        # ---- 场景 a：mock 给出的值与规则一致 ----
        print("\n[2] llm_infer + reconcile（场景 a：LLM 与规则一致）====")
        llm_a = await run_llm_infer(_MockProvider(mismatched_volume=False), sheet_dir, sheet_ids, rule_draft, relevant_layers_by_sheet=relevant)
        report_a = run_reconcile(sheet_dir, rule_draft, llm_a)
        print(json.dumps(report_a, ensure_ascii=False, indent=2))

        # ---- 场景 b：LLM 在 volume 上故意偏差 50% → 应识别为 conflict ----
        print("\n[3] llm_infer + reconcile（场景 b：volume 故意偏差 50%）====")
        llm_b = await run_llm_infer(_MockProvider(mismatched_volume=True), sheet_dir, sheet_ids, rule_draft, relevant_layers_by_sheet=relevant)
        print("    llm_b.byCategory keys:", list(llm_b["byCategory"].keys()))
        for cat in ["bored_pile", "diaphragm_wall", "cap", "retaining_pile"]:
            print(f"    llm_b[{cat}] ids:", [r.get("id") for r in llm_b["byCategory"].get(cat, [])])
        print("    rule_draft.byCategory keys:", list(rule_draft.byCategory.keys()))
        for cat in ["bored_pile", "diaphragm_wall", "cap", "retaining_pile"]:
            print(f"    rule_draft[{cat}] ids:", [r.id for r in rule_draft.byCategory.get(cat, [])])
        report_b = run_reconcile(sheet_dir, rule_draft, llm_b)
        print(json.dumps(report_b, ensure_ascii=False, indent=2))


def _run():
    asyncio.run(main())


if __name__ == "__main__":
    _run()