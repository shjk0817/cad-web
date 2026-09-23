# -*- coding: utf-8 -*-
"""工程量复核 REST + SSE。

端点：
    POST   /api/audit/tasks                      创建复核任务
    GET    /api/audit/tasks                      列出所有任务
    GET    /api/audit/tasks/{id}                 获取任务详情（含中间产物）
    POST   /api/audit/tasks/{id}/confirm-sheets  VLM 选图确认后继续推进
    POST   /api/audit/tasks/{id}/step            调试模式单步重跑
    GET    /api/audit/tasks/{id}/stream          SSE 进度推送
    GET    /api/audit/tasks/{id}/sheets/{sid}/png  下载 VLM 用的截图
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from .. import config as app_cfg
from . import pipeline as audit_pipeline
from .render import render_sheet_to_cache


log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])

# 校验：与已有 uploads 一致
_FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHEET_ID_RE = re.compile(r"^sheet_\d+$")


# --------------------------------------------------------------------------
# 创建
# --------------------------------------------------------------------------

@router.post("/tasks")
def create_task(body: Dict[str, Any]):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    file_id = body.get("fileId") or body.get("file_id") or ""
    if not _FILE_ID_RE.match(file_id):
        raise HTTPException(status_code=400, detail="fileId 不合法")
    categories = body.get("categories") or []
    if not isinstance(categories, list):
        raise HTTPException(status_code=400, detail="categories 必须是数组")
    valid_cats = {"retaining_pile", "bored_pile", "diaphragm_wall", "cap"}
    cats = [c for c in categories if c in valid_cats]
    if not cats:
        raise HTTPException(status_code=400, detail="至少选择 1 个复核类别")
    # 文件存在性
    session_dir = app_cfg.WORK_DIR / file_id
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="文件不存在或未拆分完成，请先上传")

    state = audit_pipeline.start_audit_task(
        file_id,
        cats,
        vision_provider_id=body.get("visionProviderId") or body.get("vision_provider_id") or "",
        chat_provider_id=body.get("chatProviderId") or body.get("chat_provider_id") or "",
    )
    return {"task": audit_pipeline._to_public(state)}


# --------------------------------------------------------------------------
# 详情 / 列表
# --------------------------------------------------------------------------

@router.get("/tasks")
def list_tasks():
    states = audit_pipeline.AUDIT.all()
    states.sort(key=lambda s: s.created_at, reverse=True)
    return {"tasks": [audit_pipeline._to_public(s) for s in states]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    state = audit_pipeline.AUDIT.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task": audit_pipeline._to_public(state)}


# --------------------------------------------------------------------------
# 确认图纸
# --------------------------------------------------------------------------

@router.post("/tasks/{task_id}/confirm-sheets")
def confirm_sheets(task_id: str, body: Dict[str, Any]):
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    state = audit_pipeline.AUDIT.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if state.stage not in (audit_pipeline.STAGE_AWAITING_CONFIRM, "awaiting_confirm"):
        raise HTTPException(status_code=400, detail=f"当前阶段 {state.stage} 不可 confirm")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    confirmed = body.get("sheetIds") or body.get("sheet_ids") or body.get("confirmedSheets") or []
    if not isinstance(confirmed, list) or not confirmed:
        raise HTTPException(status_code=400, detail="sheetIds 必须为非空数组")
    # 截断到合法 sheet_id
    confirmed = [s for s in confirmed if isinstance(s, str) and _SHEET_ID_RE.match(s)]
    if not confirmed:
        raise HTTPException(status_code=400, detail="sheetIds 必须全为合法 sheet id")

    audit_pipeline.resume_after_confirm(state, confirmed)
    return {"task": audit_pipeline._to_public(state)}


# --------------------------------------------------------------------------
# 调试模式单步
# --------------------------------------------------------------------------

@router.post("/tasks/{task_id}/step")
def run_step(task_id: str, body: Dict[str, Any]):
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    state = audit_pipeline.AUDIT.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    stage = body.get("stage") or ""
    if stage not in audit_pipeline.ALLOWED_DEBUG_STEPS:
        raise HTTPException(
            status_code=400,
            detail=f"未知 stage：{stage}；可选：{sorted(audit_pipeline.ALLOWED_DEBUG_STEPS)}",
        )
    try:
        audit_pipeline.run_step(state, stage)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": audit_pipeline._to_public(state)}


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------

@router.get("/tasks/{task_id}/stream")
def stream_task(task_id: str):
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    state = audit_pipeline.AUDIT.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    q = audit_pipeline.AUDIT.subscribe(task_id)
    if q is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    def event_stream():
        try:
            yield "data: %s\n\n" % json.dumps(
                audit_pipeline._to_public(state), ensure_ascii=False
            )
            if state.status in ("done", "error"):
                return

            last = (
                -1.0,
                state.stage,
                state.status,
                state.message,
                state.updated_at,
            )

            while True:
                try:
                    new_state = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue

                sig = (
                    new_state.progress,
                    new_state.stage,
                    new_state.status,
                    new_state.message,
                    new_state.updated_at,
                )
                if sig != last:
                    last = sig
                    yield "data: %s\n\n" % json.dumps(
                        audit_pipeline._to_public(new_state), ensure_ascii=False
                    )

                if new_state.status in ("done", "error"):
                    return
        finally:
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------
# Sheet PNG 下载（VLM 视觉用缓存）
# --------------------------------------------------------------------------

@router.get("/tasks/{task_id}/sheets/{sheet_id}/png")
def get_sheet_png(task_id: str, sheet_id: str):
    if not _TASK_ID_RE.match(task_id) or not _SHEET_ID_RE.match(sheet_id):
        raise HTTPException(status_code=404, detail="图纸不存在")
    state = audit_pipeline.AUDIT.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    sheet_dir = str(app_cfg.WORK_DIR / state.file_id)
    png = render_sheet_to_cache(sheet_dir, sheet_id)
    if not png or not os.path.isfile(png):
        raise HTTPException(status_code=404, detail="图纸截图未生成")
    return FileResponse(png, media_type="image/png", filename=f"{sheet_id}.png")


__all__ = ["router"]