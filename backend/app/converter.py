# -*- coding: utf-8 -*-
"""DWG → DXF 转换：对 GNU LibreDWG 的 dwg2dxf 命令行封装。"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Callable, Optional

from .config import DWG2DXF_PATH

# 子进程超时时间（秒）
_TIMEOUT = 120

# 进度轮询间隔（秒）
_PROGRESS_TICK = 0.4


class ConversionError(Exception):
    """DWG 转换为 DXF 失败。"""


def convert_dwg_to_dxf(
    dwg_path: str,
    dxf_path: str,
    on_progress: Optional[Callable[[float], None]] = None,
) -> None:
    """调用 dwg2dxf 把 DWG 转换为 DXF。

    :param dwg_path: 输入 DWG 文件路径
    :param dxf_path: 输出 DXF 文件路径
    :param on_progress: 0.0–1.0 的进度回调，每隔 _PROGRESS_TICK 推一次
                       （基于已用时长 / 总超时估算）
    :raises ConversionError: 可执行文件缺失、超时或退出码非 0
    """
    exe = DWG2DXF_PATH
    if not exe or not os.path.isfile(exe):
        raise ConversionError(
            "未找到 dwg2dxf 可执行文件，请安装 GNU LibreDWG 或设置 DWG2DXF_PATH"
        )

    # 必须在 exe 所在目录运行，否则其依赖的 dll 可能加载失败
    cwd = os.path.dirname(os.path.abspath(exe))
    cmd = [exe, "-y", "-o", dxf_path, dwg_path]

    start = time.monotonic()
    stop_flag = threading.Event()

    def _tick():
        while not stop_flag.is_set():
            elapsed = time.monotonic() - start
            # 用总时长的比例来估算进度，但封顶在 0.95 —— 真正完成后会推 1.0
            p = min(0.95, elapsed / _TIMEOUT)
            try:
                if on_progress:
                    on_progress(p)
            except Exception:
                # 回调异常不能让子进程监控线程崩溃
                pass
            stop_flag.wait(_PROGRESS_TICK)

    tick_thread: Optional[threading.Thread] = None
    if on_progress:
        tick_thread = threading.Thread(target=_tick, daemon=True)
        tick_thread.start()

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ConversionError(
            "DWG 转换超时（超过 %d 秒），文件可能过大或损坏" % _TIMEOUT
        )
    finally:
        stop_flag.set()
        if tick_thread is not None:
            tick_thread.join(timeout=_PROGRESS_TICK * 2)

    if result.returncode != 0:
        # dwg2dxf 的诊断信息主要写在 stderr，取末尾若干文本便于定位问题
        tail = result.stderr.decode("utf-8", errors="replace").strip()[-800:]
        raise ConversionError(
            "dwg2dxf 转换失败（退出码 %d）：%s" % (result.returncode, tail)
        )

    if on_progress:
        try:
            on_progress(1.0)
        except Exception:
            pass