# -*- coding: utf-8 -*-
"""手动跑规则管线的最小化 demo。

用法：
    cd backend && python3 -m app.audit.demo
"""
from __future__ import annotations

import json
import os
import tempfile

import ezdxf


def _build_bored_pile_doc(path: str) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    # 24 个圆形桩位
    for i in range(24):
        cx, cy = (i % 6) * 1000.0, (i // 6) * 1000.0
        msp.add_circle((cx, cy), radius=400)
    # 工程数量表（用文字模拟）
    rows = [
        ("数量 N=24 桩径 Φ800 长度 L=18m 体积 V=180", (10000, -500)),
        ("钻孔灌注桩布置图", (10000, 500)),
    ]
    for txt, (x, y) in rows:
        msp.add_text(txt, dxfattribs={"height": 100, "insert": (x, y)})
    doc.saveas(path)


def _build_cap_doc(path: str) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    # 一个矩形承台 + 标注
    msp.add_lwpolyline([(0, 0), (4000, 0), (4000, 1500), (0, 1500)], close=True)
    msp.add_text("承台 CT-3 数量 N=6 厚度 h=1.2m 体积 V=28.8", dxfattribs={"height": 100, "insert": (0, -300)})
    doc.saveas(path)


def main() -> None:
    from app.audit.rules import run_rules_pipeline, rule_draft_to_json

    with tempfile.TemporaryDirectory() as tmp:
        sheet_dir = os.path.join(tmp, "sheets")
        os.makedirs(sheet_dir, exist_ok=True)
        _build_bored_pile_doc(os.path.join(sheet_dir, "sheet_1.dxf"))
        _build_cap_doc(os.path.join(sheet_dir, "sheet_2.dxf"))
        manifest = {
            "sheets": [
                {"id": "sheet_1", "name": "钻孔灌注桩布置图", "layout": "Model"},
                {"id": "sheet_2", "name": "承台 CT-3", "layout": "Model"},
            ]
        }
        with open(os.path.join(sheet_dir, "manifest.json"), "w", encoding="utf-8") as fp:
            json.dump(manifest, fp, ensure_ascii=False)

        draft = run_rules_pipeline(sheet_dir)
        out = rule_draft_to_json(draft)
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()