# -*- coding: utf-8 -*-
"""LLM 模型抽象层（Provider）。

- provider：抽象基类
- openai_compat：OpenAI 兼容协议（覆盖 OpenAI / DeepSeek / 通义 / 智谱 / Moonshot / Ollama / vLLM 等）
- anthropic：Anthropic 原生协议
- gemini：Gemini 原生协议
- registry：预置 Provider 元数据 + 运行时注册（build_provider）
- store：Provider 配置 JSON 落盘
- routes：FastAPI REST 接口
"""
from __future__ import annotations

from . import (
    anthropic as anthropic_module,
    gemini as gemini_module,
    openai_compat as openai_compat_module,
    provider as provider_module,
    registry as registry_module,
    routes as routes_module,
    store as store_module,
)

__all__ = [
    "anthropic",
    "gemini",
    "openai_compat",
    "provider",
    "registry",
    "routes",
    "store",
]
