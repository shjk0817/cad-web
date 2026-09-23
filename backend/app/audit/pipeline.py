# -*- coding: utf-8 -*-
"""工程量复核 5 阶段流水线 + SSE 编排。

复用 app.tasks.TASKS 的内存 TaskManager：
    - 阶段：rules → vlm_select → awaiting_confirm → llm_infer → reconcile → done/error
    - 订阅者通过 /api/audit/tasks/:id/stream 拿 SSE
    - 「awaiting_confirm」阶段是人为停滞点，前端需要 confirm_sheets 后才往下走

数据契约（与前端 types.ts 对齐）：
    AuditTask {
        id, fileId, status, stage, progress, message,
        createdAt, updatedAt,
        categories: {rearing_pile, bored_pile, diaphragm_wall, cap},
        providers: {rulesProvider, visionProviderId, chatProviderId},
        result: {
            ruleDraft, vlmCandidates, confirmedSheets, llmResult, reconcileReport,
            steps: {rules, vlmSelect, llmInfer, reconcile}
          },
    }
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import rules as rules_mod
from . import vlm_select as vlm_select_mod
from . import llm_infer as llm_infer_mod
from . import reconcile as reconcile_mod
from ..llm.registry import build_provider
from ..llm.store import get as store_get, load_configs
from ..tasks import TASKS


log = logging.getLogger(__name__)


# 阶段常量（前后端共用字符串）
STAGE_RULES = "rules"
STAGE_VLM_SELECT = "vlm_select"
STAGE_AWAITING_CONFIRM = "awaiting_confirm"
STAGE_LLM_INFER = "llm_infer"
STAGE_RECONCILE = "reconcile"
STAGE_DONE = "done"
STAGE_ERROR = "error"

ALL_STAGES = [
    STAGE_RULES,
    STAGE_VLM_SELECT,
    STAGE_AWAITING_CONFIRM,
    STAGE_LLM_INFER,
    STAGE_RECONCILE,
    STAGE_DONE,
]


# AuditTask 在内存中的结构（用 dataclass，便于 __getstate__）
@dataclass
class AuditTaskState:
    id: str
    file_id: str
    status: str = "running"        # running / done / error / awaiting_confirm
    stage: str = "queued"
    progress: float = 0.0
    message: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 输入
    categories: List[str] = field(default_factory=list)   # 4 类选中的复核类别
    vision_provider_id: str = ""
    chat_provider_id: str = ""
    # 中间产物
    sheet_ids: List[str] = field(default_factory=list)
    sheet_names: List[str] = field(default_factory=list)
    rule_draft_json: Optional[Dict[str, Any]] = None
    vlm_candidates: List[Dict[str, Any]] = field(default_factory=list)
    confirmed_sheets: List[str] = field(default_factory=list)
    relevant_layers_by_sheet: Dict[str, List[str]] = field(default_factory=dict)
    llm_result: Optional[Dict[str, Any]] = None
    reconcile_report: Optional[Dict[str, Any]] = None
    # 单步调试用
    _step_running: bool = False
    _step_lock: threading.Lock = field(default_factory=threading.Lock)
    # 阶段历史（供 AuditStepTimeline 展示）
    steps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 错误信息
    detail: Optional[str] = None


# 单独的 Manager，避免污染 TASKS（不同生命周期 + 不需要 SSE 队列；状态推送由 SSE handler 主动查）
class AuditTaskManager:
    def __init__(self) -> None:
        self._tasks: Dict[str, AuditTaskState] = {}
        self._queues: Dict[str, "queue.Queue[AuditTaskState]"] = {}
        self._lock = threading.Lock()

    def create(self, file_id: str, categories: List[str]) -> AuditTaskState:
        tid = uuid.uuid4().hex
        state = AuditTaskState(id=tid, file_id=file_id, categories=list(categories or []))
        with self._lock:
            self._tasks[tid] = state
            self._queues[tid] = queue.Queue(maxsize=128)
        return state

    def get(self, task_id: str) -> Optional[AuditTaskState]:
        with self._lock:
            return self._tasks.get(task_id)

    def all(self) -> List[AuditTaskState]:
        with self._lock:
            return list(self._tasks.values())

    def subscribe(self, task_id: str) -> Optional["queue.Queue[AuditTaskState]"]:
        with self._lock:
            return self._queues.get(task_id)

    def publish(self, state: AuditTaskState) -> None:
        state.updated_at = time.time()
        q = self._queues.get(state.id)
        if q is None:
            return
        try:
            q.put_nowait(state)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(state)
            except Exception:
                pass


AUDIT = AuditTaskManager()


# --------------------------------------------------------------------------
# 序列化（与前端 AuditTask 对齐）
# --------------------------------------------------------------------------

def _to_public(state: AuditTaskState) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": state.id,
        "fileId": state.file_id,
        "status": state.status,
        "stage": state.stage,
        "progress": round(state.progress, 4),
        "message": state.message,
        "createdAt": state.created_at,
        "updatedAt": state.updated_at,
        "categories": list(state.categories),
        "visionProviderId": state.vision_provider_id,
        "chatProviderId": state.chat_provider_id,
        "sheetIds": list(state.sheet_ids),
        "sheetNames": list(state.sheet_names),
        "vlmCandidates": list(state.vlm_candidates),
        "confirmedSheets": list(state.confirmed_sheets),
        "steps": _steps_public(state.steps),
    }
    # 中间产物：仅在有值时回传（避免 SSE payload 过大）
    if state.rule_draft_json is not None:
        payload["ruleDraft"] = state.rule_draft_json
    if state.llm_result is not None:
        payload["llmResult"] = state.llm_result
    if state.reconcile_report is not None:
        payload["reconcileReport"] = state.reconcile_report
    if state.detail is not None:
        payload["detail"] = state.detail
    return payload


def _steps_public(steps: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """只返回稳定字段，避免 dataclass 内部 lock 出现在 payload 中。"""
    out: Dict[str, Any] = {}
    for k, v in steps.items():
        if not isinstance(v, dict):
            continue
        out[k] = {
            "stage": v.get("stage", k),
            "status": v.get("status", "pending"),
            "startedAt": v.get("startedAt"),
            "finishedAt": v.get("finishedAt"),
            "message": v.get("message", ""),
            # 阶段输出的中间 payload：仅当存在时附带
            "payload": v.get("payload"),
        }
    return out


# --------------------------------------------------------------------------
# Provider 工厂
# --------------------------------------------------------------------------

def _resolve_provider(provider_id: str) -> Any:
    if not provider_id:
        return None
    cfg = store_get(provider_id)
    if cfg is None:
        raise ValueError(f"未找到 Provider 配置：{provider_id}")
    return build_provider(cfg)


def _list_enabled() -> List[Dict[str, Any]]:
    return [c for c in load_configs() if c.get("enabled")]


# --------------------------------------------------------------------------
# 阶段执行
# --------------------------------------------------------------------------

def _set_step(state: AuditTaskState, name: str, **kw: Any) -> None:
    s = state.steps.get(name, {"stage": name})
    s.update(kw)
    state.steps[name] = s


def _report(state: AuditTaskState, stage: str, progress: float, message: str) -> None:
    state.stage = stage
    state.progress = max(state.progress, progress)
    state.message = message
    AUDIT.publish(state)


def _sheet_dir(file_id: str) -> str:
    from .. import config as app_cfg
    return str(app_cfg.WORK_DIR / file_id)


def _load_sheet_metadata(file_id: str) -> List[Dict[str, Any]]:
    """读取 manifest.json，返回 [{id, name}, ...]。"""
    from .. import config as app_cfg
    p = app_cfg.WORK_DIR / file_id / "manifest.json"
    if not p.is_file():
        return []
    try:
        with open(p, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        sheets = data.get("sheets", []) if isinstance(data, dict) else []
        return [
            {"id": s["id"], "name": s.get("name", s["id"])}
            for s in sheets if s.get("id")
        ]
    except Exception:
        return []


# ---------- 阶段① ----------
def _run_rules(state: AuditTaskState) -> None:
    sheet_dir = _sheet_dir(state.file_id)
    _set_step(state, STAGE_RULES, status="running", startedAt=time.time())
    _report(state, STAGE_RULES, 0.05, "正在跑规则管线")

    def _cb(i: int, total: int) -> None:
        _report(state, STAGE_RULES, 0.05 + (i / max(total, 1)) * 0.15, f"规则管线 {i}/{total}")

    rd = rules_mod.run_rules_pipeline(sheet_dir, on_progress=_cb)
    state.rule_draft_json = rules_mod.rule_draft_to_json(rd)
    _set_step(
        state, STAGE_RULES, status="done", finishedAt=time.time(),
        message=f"规则管线完成（{sum(len(r) for r in rd.byCategory.values())} 行）",
        payload={"summary": rd.summary},
    )


# ---------- 阶段② ----------
async def _run_vlm_select(state: AuditTaskState) -> None:
    sheet_dir = _sheet_dir(state.file_id)
    metadata = _load_sheet_metadata(state.file_id)
    state.sheet_ids = [m["id"] for m in metadata]
    state.sheet_names = [m["name"] for m in metadata]

    _set_step(state, STAGE_VLM_SELECT, status="running", startedAt=time.time())
    _report(state, STAGE_VLM_SELECT, 0.25, "正在用 VLM 选图")

    provider = _resolve_provider(state.vision_provider_id) if state.vision_provider_id else None
    if provider is None:
        # 无 provider：所有 sheet 全部 candidate（score=0.5），让前端按 4 类自己筛
        cands: List[Dict[str, Any]] = []
        for sid in state.sheet_ids:
            cands.append({
                "sheetId": sid, "score": 0.5, "role": "other",
                "reason": "未配置 VLM，请人工确认",
                "relevantLayers": [],
            })
        state.vlm_candidates = cands
    else:
        def _cb(i: int, total: int, msg: str) -> None:
            _report(state, STAGE_VLM_SELECT, 0.25 + (i / max(total, 1)) * 0.20, msg)

        cands = await vlm_select_mod.run_vlm_selection(
            provider, sheet_dir, state.sheet_ids, on_progress=_cb,
        )
        state.vlm_candidates = cands

    # 聚合 relevantLayers
    state.relevant_layers_by_sheet = {
        c["sheetId"]: list(c.get("relevantLayers") or []) for c in state.vlm_candidates
    }
    _set_step(
        state, STAGE_VLM_SELECT, status="done", finishedAt=time.time(),
        message=f"VLM 选图 {len(state.vlm_candidates)} 张",
        payload={"candidates": state.vlm_candidates},
    )


# ---------- 阶段②→③ 阻塞点 ----------
def _awaiting_confirm(state: AuditTaskState) -> None:
    """切到 awaiting_confirm 阶段并阻塞，直到 confirm_sheets 被调用。"""
    _set_step(state, STAGE_AWAITING_CONFIRM, status="running", startedAt=time.time())
    state.status = "awaiting_confirm"
    state.stage = STAGE_AWAITING_CONFIRM
    state.progress = max(state.progress, 0.5)
    state.message = "请在前端确认 VLM 选中的图纸"
    AUDIT.publish(state)


def resume_after_confirm(state: AuditTaskState, confirmed: List[str]) -> None:
    """被 routes 调用：前端 confirm 后继续推进。"""
    if state.status not in ("awaiting_confirm", "running"):
        return
    with state._step_lock:
        if not state._step_running:
            # 直接用 main thread 的 request 线程跑后续阶段，避免重复启动后台
            t = threading.Thread(
                target=_run_post_confirm,
                args=(state, list(confirmed or [])),
                daemon=True,
            )
            state._step_running = True
            t.start()


def _run_post_confirm(state: AuditTaskState, confirmed: List[str]) -> None:
    try:
        state.confirmed_sheets = list(confirmed or [])
        _set_step(state, STAGE_AWAITING_CONFIRM, status="done",
                 finishedAt=time.time(),
                 message=f"已确认 {len(state.confirmed_sheets)} 张图纸",
                 payload={"confirmedSheets": state.confirmed_sheets})
        # 阶段③
        _run_llm_infer_sync(state)
        # 阶段④
        _run_reconcile_sync(state)
        # 完结
        state.status = "done"
        state.stage = STAGE_DONE
        state.progress = 1.0
        state.message = "工程量复核完成"
        _set_step(state, STAGE_DONE, status="done", finishedAt=time.time(),
                  message="完成", payload=None)
        AUDIT.publish(state)
    except Exception as exc:
        log.exception("audit post-confirm failed")
        state.status = "error"
        state.stage = STAGE_ERROR
        state.detail = f"复核失败：{exc}"
        _set_step(state, STAGE_RECONCILE if state.stage in (STAGE_LLM_INFER, STAGE_RECONCILE) else STAGE_LLM_INFER,
                  status="error", finishedAt=time.time(), message=state.detail)
        AUDIT.publish(state)
    finally:
        with state._step_lock:
            state._step_running = False


# ---------- 阶段③ ----------
def _run_llm_infer_sync(state: AuditTaskState) -> None:
    if not state.confirmed_sheets:
        raise ValueError("未确认任何图纸，无法推理")
    sheet_dir = _sheet_dir(state.file_id)

    _set_step(state, STAGE_LLM_INFER, status="running", startedAt=time.time())
    _report(state, STAGE_LLM_INFER, 0.55, "正在调文本 LLM 推理")

    # 重建 rule_draft 对象（来自 rule_draft_json）
    rule_draft = _rdict_from_json(state.rule_draft_json or {"byCategory": {}})
    provider = _resolve_provider(state.chat_provider_id) if state.chat_provider_id else None

    if provider is None:
        # 无 provider：所有 sheet 全部确认；llm_result 用规则管线兜底
        state.llm_result = llm_infer_mod._fallback_from_rule_draft(rule_draft)
    else:
        async def _invoke() -> int:
            def _cb(i: int, total: int, msg: str) -> None:
                _report(state, STAGE_LLM_INFER, 0.55 + (i / max(total, 1)) * 0.20, msg)
            return await llm_infer_mod.run_llm_infer(
                provider, sheet_dir, state.confirmed_sheets, rule_draft,
                relevant_layers_by_sheet=state.relevant_layers_by_sheet,
                on_progress=_cb,
            )

        try:
            state.llm_result = asyncio_run_in_thread(_invoke)
        except Exception as exc:
            log.warning("llm_infer failed: %s", exc)
            state.llm_result = llm_infer_mod._fallback_from_rule_draft(rule_draft)

    _set_step(
        state, STAGE_LLM_INFER, status="done", finishedAt=time.time(),
        message=f"LLM 推理完成（{sum(len(v) for v in (state.llm_result or {}).get('byCategory', {}).values())} 行）",
        payload={"byCategory": state.llm_result and state.llm_result.get("byCategory")},
    )


# ---------- 阶段④ ----------
def _run_reconcile_sync(state: AuditTaskState) -> None:
    if state.llm_result is None or state.rule_draft_json is None:
        raise ValueError("LLM 推理未完成")
    sheet_dir = _sheet_dir(state.file_id)
    rule_draft = _rdict_from_json(state.rule_draft_json)

    _set_step(state, STAGE_RECONCILE, status="running", startedAt=time.time())
    _report(state, STAGE_RECONCILE, 0.78, "正在三方对账")

    report = reconcile_mod.run_reconcile(sheet_dir, rule_draft, state.llm_result)
    state.reconcile_report = report
    _set_step(
        state, STAGE_RECONCILE, status="done", finishedAt=time.time(),
        message=f"对账完成 (置信度 {report.get('confidence', 0)})",
        payload={"summary": report.get("summary"), "confidence": report.get("confidence")},
    )


# --------------------------------------------------------------------------
# 把 rule_draft_json 反序列化为 RuleDraftDict-like 对象（足够 reconcile 消费）
# --------------------------------------------------------------------------

def _rdict_from_json(d: Dict[str, Any]) -> rules_mod.RuleDraftDict:
    summary = d.get("summary") or {}
    by_cat: Dict[str, List[rules_mod.RuleDraftRow]] = {}
    for cat in rules_mod.CATEGORIES:
        items = (d.get("byCategory") or {}).get(cat, [])
        rows: List[rules_mod.RuleDraftRow] = []
        for it in items:
            row = rules_mod.RuleDraftRow(
                id=str(it.get("id") or ""),
                fields=dict(it.get("fields") or {}),
            )
            rows.append(row)
        by_cat[cat] = rows
    return rules_mod.RuleDraftDict(byCategory=by_cat, summary=summary)


# --------------------------------------------------------------------------
# 把 coroutine 跑在后台（避免 routes 里再加 asyncio 上下文）
# --------------------------------------------------------------------------

import asyncio

def asyncio_run_in_thread(coro_factory) -> Any:
    """在独立线程上跑 async 协程并等待结果。

    限制：每次调用最多跑一个协程；不能嵌套。生产中可改 asyncio.run_coroutine_threadsafe。
    """
    box: Dict[str, Any] = {"result": None, "error": None}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro_factory())
        except Exception as exc:
            box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if box["error"] is not None:
        raise box["error"]
    return box["result"]


# --------------------------------------------------------------------------
# 启动入口（routes 调用）
# --------------------------------------------------------------------------

def start_audit_task(
    file_id: str,
    categories: List[str],
    *,
    vision_provider_id: str = "",
    chat_provider_id: str = "",
) -> AuditTaskState:
    """异步跑全流水线（rules → vlm_select → awaiting_confirm → 等前端 confirm）。"""
    state = AUDIT.create(file_id, categories)
    state.vision_provider_id = vision_provider_id
    state.chat_provider_id = chat_provider_id

    def _run() -> None:
        try:
            _run_rules(state)
            _run_async_in_thread(state, _run_vlm_select)
            _awaiting_confirm(state)
        except Exception as exc:
            log.exception("audit pipeline init failed")
            state.status = "error"
            state.stage = STAGE_ERROR
            state.detail = f"启动失败：{exc}"
            AUDIT.publish(state)

    threading.Thread(target=_run, daemon=True).start()
    return state


def _run_async_in_thread(state: AuditTaskState, async_fn) -> None:
    """在子线程里跑一个 async 函数（vlm_select / llm_infer 等）。"""
    def _wrapper() -> None:
        try:
            asyncio.run(async_fn(state))
        except Exception as exc:
            log.exception("async stage failed: %s", async_fn)
            state.status = "error"
            state.stage = STAGE_ERROR
            state.detail = f"阶段 {async_fn.__name__} 失败：{exc}"
            AUDIT.publish(state)

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    t.join()


# --------------------------------------------------------------------------
# 调试：单步重跑
# --------------------------------------------------------------------------

ALLOWED_DEBUG_STEPS = {STAGE_RULES, STAGE_VLM_SELECT, STAGE_LLM_INFER, STAGE_RECONCILE}


def run_step(state: AuditTaskState, stage: str) -> None:
    """调试模式单步重跑；不切换 stage、不阻塞 awaiting_confirm。"""
    if stage not in ALLOWED_DEBUG_STEPS:
        raise ValueError(f"未知阶段：{stage}")
    if stage in (STAGE_RULES, STAGE_VLM_SELECT) and not state.confirmed_sheets:
        # 阶段①② 可以单独跑；其他阶段需要先 confirm
        pass

    def _run() -> None:
        try:
            if stage == STAGE_RULES:
                _run_rules(state)
            elif stage == STAGE_VLM_SELECT:
                _run_async_in_thread(state, _run_vlm_select)
            elif stage == STAGE_LLM_INFER:
                if not state.confirmed_sheets:
                    raise ValueError("LLM 推理前请先 confirm_sheets")
                _run_llm_infer_sync(state)
            elif stage == STAGE_RECONCILE:
                _run_reconcile_sync(state)
            _report(state, state.stage, state.progress, f"调试：{stage} 已完成")
        except Exception as exc:
            log.exception("debug step %s failed", stage)
            state.status = "error"
            state.stage = STAGE_ERROR
            state.detail = f"单步重跑失败：{exc}"
            AUDIT.publish(state)

    threading.Thread(target=_run, daemon=True).start()


__all__ = [
    "AUDIT",
    "AuditTaskState",
    "ALL_STAGES",
    "STAGE_RULES",
    "STAGE_VLM_SELECT",
    "STAGE_AWAITING_CONFIRM",
    "STAGE_LLM_INFER",
    "STAGE_RECONCILE",
    "STAGE_DONE",
    "STAGE_ERROR",
    "start_audit_task",
    "resume_after_confirm",
    "run_step",
    "_to_public",
]