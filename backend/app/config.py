# -*- coding: utf-8 -*-
"""全局配置：dwg2dxf 路径、工作目录、文件保留时长。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# 本文件位于 backend/app/config.py，项目根目录为其上两级
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# 工作目录：上传文件与中间产物存放处，默认 backend/tmp
WORK_DIR = Path(os.environ.get("WORK_DIR", str(BACKEND_DIR / "tmp"))).resolve()

# 会话文件保留小时数，默认 24 小时
TTL_HOURS = float(os.environ.get("TTL_HOURS", "24"))

# dwg2dxf 可执行文件名（Windows 下为 .exe）
_EXE_NAME = "dwg2dxf.exe" if os.name == "nt" else "dwg2dxf"


def _detect_dwg2dxf() -> str | None:
    """按优先级探测 dwg2dxf 路径：
    1. 环境变量 DWG2DXF_PATH；
    2. 项目根 tools/libredwg/ 下的可执行文件；
    3. PATH 中的 dwg2dxf。
    """
    env_path = os.environ.get("DWG2DXF_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)

    local_path = PROJECT_ROOT / "tools" / "libredwg" / _EXE_NAME
    if local_path.is_file():
        return str(local_path)

    on_path = shutil.which("dwg2dxf")
    if on_path:
        return on_path

    return None


# dwg2dxf 可执行文件路径（可能为 None，表示未安装/未配置）
DWG2DXF_PATH = _detect_dwg2dxf()
