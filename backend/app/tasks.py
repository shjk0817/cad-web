# -*- coding: utf-8 -*-
"""解析任务状态机与 SSE 推送。

- POST /api/dwg/upload 返回 taskId，立即把上传字节落到会话目录后
  异步跑「读 hash → 转换 → 解析 → 拆分 → 写缓存」流水线。
- 阶段通过内存 dict 暴露，前端用 SSE 订阅。

阶段（stage）：
- "queued"          已入队，未开始
- "hashing"         正在计算 SHA-256
- "converting"      dwg2dxf 转换中（子进程）
- "loading"         ezdxf.readfile
- "detecting"       图框检测
- "splitting"       拆分并写出 sheet_N.dxf（progress: 当前 sheet/总 sheet）
- "indexing"        写缓存索引
- "done"            完成（progress=1.0），data 中带 sheets/fileId/cached
- "error"           失败，data 中带 detail
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskState:
    task_id: str
    status: str = "running"      # running / done / error
    stage: str = "queued"
    progress: float = 0.0         # 0.0 - 1.0
    message: str = ""
    file_id: Optional[str] = None
    cached: bool = False
    sheets: list = field(default_factory=list)
    detail: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


class TaskManager:
    """简易内存任务管理器：单进程适用，多 worker 部署时需换 Redis。"""

    def __init__(self, maxsize: int = 64) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._queues: dict[str, "queue.Queue[TaskState]"] = {}
        self._lock = threading.Lock()

    def create(self, maxsize: int = 64) -> TaskState:
        tid = uuid.uuid4().hex
        state = TaskState(task_id=tid)
        with self._lock:
            self._tasks[tid] = state
            self._queues[tid] = queue.Queue(maxsize=maxsize)
        return state

    def get(self, task_id: str) -> Optional[TaskState]:
        with self._lock:
            return self._tasks.get(task_id)

    def subscribe(self, task_id: str) -> Optional["queue.Queue[TaskState]"]:
        with self._lock:
            return self._queues.get(task_id)

    def publish(self, state: TaskState) -> None:
        """更新状态并向所有订阅者推一份快照（非阻塞）。"""
        state.updated_at = time.time()
        q = self._queues.get(state.task_id)
        if q is not None:
            # 队列满时丢弃最旧，确保最新进度总能送到
            while True:
                try:
                    q.put_nowait(state)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

    def finish(self, task_id: str) -> None:
        with self._lock:
            q = self._queues.pop(task_id, None)
        if q is not None:
            # 推一个 sentinel：调用方看到 status in {"done", "error"} 就退出
            try:
                q.put_nowait(self._tasks[task_id])
            except Exception:
                pass

    def cleanup(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)
            self._queues.pop(task_id, None)


# 全局单例
TASKS = TaskManager()