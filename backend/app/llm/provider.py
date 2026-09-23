# -*- coding: utf-8 -*-
"""LLM Provider 抽象基类。

所有具体协议（OpenAI 兼容 / Anthropic 原生 / Gemini 原生）实现各自子类。

Provider 配置（config dict）字段约定：
    id          str        实例 ID（uuid4 hex，重启后保持）
    presetId    str        预置 Provider ID（如 'openai' / 'anthropic' 等）
    name        str        用户可读的实例名称
    protocol    str        协议类型：'openai' | 'anthropic' | 'gemini'
    baseUrl     str        自定义 baseUrl（不含尾随 /）
    apiKey      str        访问密钥（不回显给前端以外的调用方）
    chatModel   str        默认 chat 模型 ID
    visionModel str|None   vision 模型 ID；空表示不支持/不配置
    enabled     bool       是否启用
"""
from __future__ import annotations

from abc import ABC
from typing import AsyncIterator


class Provider(ABC):
    """所有具体 Provider 协议的抽象父类。"""

    # 由子类覆盖的元数据
    name: str = ""
    protocol: str = ""  # 'openai' | 'anthropic' | 'gemini'

    def __init__(self, config: dict) -> None:
        """构造方法：接收原始配置 dict。

        子类可以从配置中读取 baseUrl / apiKey / model 等等。
        至少应当保存整份 config 以备使用。
        """
        self.config = config or {}

    # ----- 通用能力 -----

    @property
    def id(self) -> str:
        """Provider 实例 ID（来自 config）。"""
        return self.config.get("id", "")

    @property
    def api_key(self) -> str:
        return self.config.get("apiKey", "") or ""

    @property
    def base_url(self) -> str:
        """默认 baseUrl。子类可覆盖（例如 Gemini 拼接版本段）。"""
        return self.config.get("baseUrl", "") or ""

    @property
    def chat_model(self) -> str:
        return self.config.get("chatModel", "") or ""

    @property
    def vision_model(self) -> str:
        return self.config.get("visionModel", "") or self.chat_model

    def get_chat_model(self, override: str | None = None) -> str:
        return override or self.chat_model

    def get_vision_model(self, override: str | None = None) -> str:
        return override or self.vision_model

    # ----- 接口能力（强制子类实现） -----

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """非流式聊天：输入 OpenAI 风格 messages（role/content），输出最终助手文本。"""
        raise NotImplementedError

    async def vision(
        self,
        images: list[bytes],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        """视觉问答：输入图像字节列表 + 文本 prompt，返回 LLM 输出文本。"""
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: list[dict],
    ) -> AsyncIterator[str]:
        """流式聊天：可选能力。"""
        raise NotImplementedError
        yield ""  # pragma: no cover - 显式让子类覆盖

    async def test(self) -> tuple[bool, str]:
        """连通性自检：返回 (success, message)。

        失败时 message 应包含人类可读原因，便于前端展示。
        """
        raise NotImplementedError
