# -*- coding: utf-8 -*-
"""FastAPI 应用：DWG 上传、转换拆分、图纸 DXF 下载。

流水线按「阶段」上报进度，前端通过 SSE 订阅实时获取阶段变化。
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager

import ezdxf
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse

from . import config
from .converter import ConversionError, convert_dwg_to_dxf
from .frames import detect_frames
from .llm.routes import router as llm_router
from .audit.routes import router as audit_router
from .splitter import split_document
from .tasks import TASKS, TaskState

# file_id：32 位十六进制
_FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# sheet_id：sheet_数字
_SHEET_ID_RE = re.compile(r"^sheet_\d+$")
# 安全文件名只保留常见中英文、数字与部分符号
_SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')

# 内容 hash → file_id 的缓存索引（文件形式持久化）
_CACHE_INDEX = config.WORK_DIR / "cache_index.json"
_cache_lock = threading.Lock()


def _load_cache_index() -> dict:
    try:
        with open(_CACHE_INDEX, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache_index(index: dict) -> None:
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_INDEX.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(index, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, _CACHE_INDEX)


def _cleanup_expired() -> None:
    """删除工作目录中修改时间超过 TTL_HOURS 的会话子目录，并同步缓存索引。"""
    if not config.WORK_DIR.exists():
        return
    deadline = time.time() - config.TTL_HOURS * 3600
    expired_ids: set = set()

    for entry in config.WORK_DIR.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < deadline:
                expired_ids.add(entry.name)
                shutil.rmtree(entry, ignore_errors=True)
        except Exception:
            continue

    if expired_ids and _CACHE_INDEX.is_file():
        with _cache_lock:
            index = _load_cache_index()
            changed = False
            for key, value in list(index.items()):
                if value.get("fileId") in expired_ids:
                    del index[key]
                    changed = True
            if changed:
                _save_cache_index(index)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时清理过期目录
    _cleanup_expired()
    yield


app = FastAPI(title="DWG 多图框拆分服务", lifespan=lifespan)

# 开发阶段允许全部来源跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    """健康检查，同时返回 dwg2dxf 路径（未配置为 null）。"""
    return {"ok": True, "dwg2dxf": config.DWG2DXF_PATH}


# 多 Provider 模型抽象层 REST 接口（/api/llm/providers/...）
app.include_router(llm_router)

# 工程量复核 REST + SSE（/api/audit/...）
app.include_router(audit_router)


def _sheets_payload(sheets: list) -> list:
    """抽取返回给前端的图纸摘要字段。"""
    return [
        {"id": s["id"], "name": s["name"],
         "width": s["width"], "height": s["height"]}
        for s in sheets
    ]


def _serialize(state: TaskState) -> dict:
    """生成 SSE 事件载荷。"""
    payload = {
        "taskId": state.task_id,
        "status": state.status,
        "stage": state.stage,
        "progress": round(state.progress, 4),
        "message": state.message,
    }
    if state.status == "done":
        payload["fileId"] = state.file_id
        payload["cached"] = state.cached
        payload["sheets"] = _sheets_payload(state.sheets)
    elif state.status == "error":
        payload["detail"] = state.detail
    return payload


def _run_pipeline(task_id: str, data: bytes, filename: str) -> None:
    """后台线程：解析 DWG → 拆分 → 写缓存。每个阶段向 SSE 订阅者推送进度。"""
    state = TASKS.get(task_id)
    if state is None:
        return

    def report(stage: str, progress: float, message: str = "") -> None:
        state.stage = stage
        state.progress = max(state.progress, progress)
        state.message = message
        TASKS.publish(state)

    try:
        # 1. 计算 SHA-256（一次性读，对小文件秒级；大文件仍按字节流走）
        report("hashing", 0.05, "正在计算文件指纹")
        content_hash = hashlib.sha256(data).hexdigest()

        # 2. 缓存命中：直接复用
        with _cache_lock:
            cached = _load_cache_index().get(content_hash)
        if cached:
            file_id = cached.get("fileId")
            session_dir = config.WORK_DIR / file_id
            manifest_path = session_dir / "manifest.json"
            if (
                isinstance(file_id, str)
                and _FILE_ID_RE.match(file_id)
                and manifest_path.is_file()
            ):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as fp:
                        manifest = json.load(fp)
                    os.utime(session_dir, None)
                    sheets = manifest.get("sheets", [])
                    state.file_id = file_id
                    state.cached = True
                    state.sheets = sheets
                    state.status = "done"
                    state.stage = "done"
                    state.progress = 1.0
                    state.message = "命中缓存"
                    TASKS.publish(state)
                    return
                except Exception:
                    pass

        # 3. 缓存未命中：落盘
        file_id = uuid.uuid4().hex
        session_dir = config.WORK_DIR / file_id
        session_dir.mkdir(parents=True, exist_ok=True)
        dwg_path = session_dir / "input.dwg"
        dxf_path = session_dir / "model.dxf"

        with open(dwg_path, "wb") as fp:
            fp.write(data)

        # 4. DWG → DXF（子进程可能耗时数秒到数十秒）
        report("converting", 0.15, "正在转换 DWG 为 DXF")
        try:
            convert_dwg_to_dxf(
                str(dwg_path), str(dxf_path),
                on_progress=lambda p: report(
                    "converting", 0.15 + p * 0.35,
                    "正在转换 DWG 为 DXF",
                ),
            )
        except ConversionError as exc:
            message = str(exc)
            # 可执行文件缺失属于服务端配置错误
            status = 500 if "未找到 dwg2dxf" in message else 400
            raise HTTPException(
                status_code=status, detail="DWG 转换失败：" + message
            )

        # 5. 解析 DXF
        report("loading", 0.55, "正在解析 DXF")
        try:
            doc = ezdxf.readfile(str(dxf_path))
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="DXF 解析失败：" + str(exc)
            )

        # 6. 图框检测
        report("detecting", 0.65, "正在识别图框")
        frames = detect_frames(doc)

        # 7. 拆分（最耗时环节，逐 sheet 推进度）
        report("splitting", 0.75, "正在拆分图纸")

        def _on_sheet(i: int, total: int) -> None:
            base = 0.75
            span = 0.20
            p = base + (i / max(total, 1)) * span
            report("splitting", p, f"正在写出第 {i + 1}/{total} 张图纸")

        # 拆分阶段前置：把 0.65–0.75 区间分给"扫描布局归集实体"
        # （对上百个图元的 DWG，xref 之前的那一步可能耗时数秒），
        # 0.75–0.95 给"按图框写出独立 DXF"，给前端持续的视觉反馈。
        def _on_assign(cur: int, tot: int) -> None:
            base = 0.65
            span = 0.10
            p = base + (cur / max(tot, 1)) * span
            report("detecting", p, f"正在按图框归集图元（{cur}/{tot}）")

        sheets = split_document(
            doc, frames, str(session_dir),
            on_sheet=_on_sheet,
            on_assign_progress=_on_assign,
        )

        # 8. 写缓存索引
        report("indexing", 0.97, "正在保存缓存索引")
        with _cache_lock:
            index = _load_cache_index()
            index[content_hash] = {"fileId": file_id, "createdAt": time.time()}
            _save_cache_index(index)

        state.file_id = file_id
        state.cached = False
        state.sheets = sheets
        state.status = "done"
        state.stage = "done"
        state.progress = 1.0
        state.message = "完成"
        TASKS.publish(state)
    except HTTPException as exc:
        state.status = "error"
        state.stage = "error"
        state.detail = exc.detail
        TASKS.publish(state)
    except Exception as exc:
        state.status = "error"
        state.stage = "error"
        state.detail = "处理失败：" + str(exc)
        TASKS.publish(state)
    finally:
        # 不在这里立即清理 task 内存，给前端留时间收尾 SSE 推送
        pass


@app.post("/api/dwg/upload")
async def upload_dwg(file: UploadFile = File(...)):
    """接收上传，立即落盘 + 启动后台任务，返回 taskId。

    前端拿到 taskId 后订阅 GET /api/dwg/tasks/{taskId} 获取阶段、进度。
    """
    filename = file.filename
    if not filename or not filename.lower().endswith(".dwg"):
        raise HTTPException(status_code=400, detail="仅支持 .dwg 文件")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")

    state = TASKS.create()
    # 启动后台线程跑流水线，主线程立即返回 taskId
    t = threading.Thread(
        target=_run_pipeline,
        args=(state.task_id, data, filename),
        daemon=True,
    )
    t.start()

    return {"taskId": state.task_id}


@app.get("/api/dwg/tasks/{task_id}")
def stream_task(task_id: str):
    """SSE：流式返回该任务的进度，直到完成或出错。

    完成时会发送一条 status=done 的事件，前端拿到后关闭连接。
    """
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    state = TASKS.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    q = TASKS.subscribe(task_id)
    if q is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    def event_stream():
        try:
            # 立刻推一份当前快照（前端打开订阅时立即拿到阶段）
            yield "data: %s\n\n" % json.dumps(
                _serialize(state), ensure_ascii=False
            )
            if state.status in ("done", "error"):
                return

            last_progress = state.progress
            while True:
                try:
                    # 用短超时，配合客户端 heartbeat；状态变化立即返回
                    new_state = q.get(timeout=15)
                except queue.Empty:
                    # 心跳：保持连接
                    yield ": ping\n\n"
                    continue

                # 同一份状态可能重复推，只在有变化时推送
                if (
                    new_state.progress != last_progress
                    or new_state.stage != state.stage
                    or new_state.status != state.status
                    or new_state.message != state.message
                ):
                    state.stage = new_state.stage
                    state.progress = new_state.progress
                    state.status = new_state.status
                    state.message = new_state.message
                    state.file_id = new_state.file_id
                    state.cached = new_state.cached
                    state.sheets = new_state.sheets
                    state.detail = new_state.detail
                    last_progress = state.progress
                    yield "data: %s\n\n" % json.dumps(
                        _serialize(state), ensure_ascii=False
                    )

                if state.status in ("done", "error"):
                    return
        finally:
            # 不立即清，给前端短暂窗口拿到 done 之后
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _safe_filename(name: str) -> str:
    """清洗为安全文件名，避免非法字符。"""
    cleaned = _SAFE_NAME_RE.sub("_", name).strip()
    return cleaned or "sheet"


@app.get("/api/dwg/{file_id}/sheets/{sheet_id}")
def get_sheet(file_id: str, sheet_id: str):
    """下载指定图纸的 DXF 文件。"""
    # 校验格式，防止路径穿越
    if not _FILE_ID_RE.match(file_id) or not _SHEET_ID_RE.match(sheet_id):
        raise HTTPException(status_code=404, detail="图纸不存在")

    session_dir = config.WORK_DIR / file_id
    sheet_path = session_dir / ("%s.dxf" % sheet_id)
    manifest_path = session_dir / "manifest.json"

    if not sheet_path.is_file() or not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="图纸不存在")

    # 从 manifest 读取图名
    try:
        with open(manifest_path, "r", encoding="utf-8") as fp:
            manifest = json.load(fp)
        name = next(
            (s["name"] for s in manifest.get("sheets", [])
             if s.get("id") == sheet_id),
            sheet_id,
        )
    except Exception:
        name = sheet_id

    safe_name = _safe_filename(name)
    return FileResponse(
        str(sheet_path),
        media_type="image/vnd.dxf",
        filename="%s.dxf" % safe_name,
    )