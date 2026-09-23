# -*- coding: utf-8 -*-
"""工程量审计（Quantity Audit）子模块。

阶段：
    - rules:       纯几何 + 标注/表格聚合推断，输出 RuleDraft；
    - render:      DXF → PNG（ezdxf.addons.drawing + MatplotlibBackend）；
    - vlm_select:  VLM 选图（多模态），输出 VlmCandidate；
    - llm_infer:   文本 LLM 在选中的图上做推理；
    - reconcile:   三方对账（geometry ↔ table ↔ annotation）；
    - pipeline:    5 阶段编排 + SSE 状态机（复用 app.tasks）；
    - routes:     REST + 任务详情/SSE/确认/单步/截图下载。
"""

from .rules import (
    CATEGORIES,
    CATEGORY_FIELDS,
    RuleDraftDict,
    RuleDraftRow,
    SheetAnnotations,
    rule_draft_to_json,
    run_rules_pipeline,
)
from .render import render_dxf_to_png, render_sheet_to_cache
from .vlm_select import build_contact_sheet, run_vlm_selection
from .llm_infer import run_llm_infer
from .reconcile import run_reconcile


__all__ = [
    # rules
    "CATEGORIES",
    "CATEGORY_FIELDS",
    "RuleDraftDict",
    "RuleDraftRow",
    "SheetAnnotations",
    "rule_draft_to_json",
    "run_rules_pipeline",
    # render
    "render_dxf_to_png",
    "render_sheet_to_cache",
    # vlm_select
    "build_contact_sheet",
    "run_vlm_selection",
    # llm_infer
    "run_llm_infer",
    # reconcile
    "run_reconcile",
]