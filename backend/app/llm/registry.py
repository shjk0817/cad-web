# -*- coding: utf-8 -*-
"""预置 Provider 元数据 + 运行时注册。

约定：
    - PRESETS：内置常量列表，每个包含 presetId 同名 id，方便前端枚举；
    - build_provider(config)：根据 config['protocol'] 实例化对应 Provider。

设计原则（2026-09 国产优先）：
    - 预置列表只列国产 + 本地（Ollama / vLLM）；
    - 国外（OpenAI / Anthropic / Gemini）不再预置，需要时通过「自定义」接入；
    - 主流国产厂商均已支持视觉/多模态，PRESETS 中 supportsVision 全部为 True；
    - 同一厂商提供多档模型时，把当前最适合工程量复核场景的「视觉主力」放
      defaultVisionModel，文本主力放 defaultChatModel。
"""
from __future__ import annotations

from typing import Any

from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider
from .provider import Provider


PRESETS: list[dict] = [
    # ---- 国产云端 ----
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "protocol": "openai",
        "defaultBaseUrl": "https://api.deepseek.com/v1",
        "defaultChatModel": "deepseek-chat",
        # V4.1-Flash（2026-09-10）原生多模态视觉理解
        "defaultVisionModel": "deepseek-flash",
        "supportsVision": True,
        "docUrl": "https://platform.deepseek.com/api-docs/",
    },
    {
        "id": "qwen",
        "name": "通义千问（DashScope, OpenAI 兼容）",
        "protocol": "openai",
        "defaultBaseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "defaultChatModel": "qwen3-max",
        # qwen-vl-max / qwen3-vl-max：超大规模视觉语言模型
        "defaultVisionModel": "qwen-vl-max",
        "supportsVision": True,
        "docUrl": "https://help.aliyun.com/zh/dashscope/developer-reference/api-details",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM（BigModel）",
        "protocol": "openai",
        "defaultBaseUrl": "https://open.bigmodel.cn/api/paas/v4/",
        "defaultChatModel": "glm-5.3",
        # GLM-4.6V（开源 9B/106B，原生工具调用）+ GLM-5.3-flash 多模态
        "defaultVisionModel": "glm-4.6v",
        "supportsVision": True,
        "docUrl": "https://bigmodel.cn/dev/api/normal-model/glm-4",
    },
    {
        "id": "doubao",
        "name": "豆包 Doubao（火山方舟, OpenAI 兼容）",
        "protocol": "openai",
        "defaultBaseUrl": "https://ark.cn-beijing.volces.com/api/v3/",
        "defaultChatModel": "doubao-seed-2-1-pro",
        # Seed 2.1 Pro / Seed-Evolving 支持深度思考 + 多模态
        "defaultVisionModel": "doubao-seed-2-1-pro",
        "supportsVision": True,
        "docUrl": "https://www.volcengine.com/docs/82379",
    },
    {
        "id": "ernie",
        "name": "文心 ERNIE（百度千帆）",
        "protocol": "openai",
        "defaultBaseUrl": "https://qianfan.baidubce.com/v2/",
        "defaultChatModel": "ernie-5.0",
        # ERNIE 5.0（2026-01-22）原生全模态；开源版 ERNIE 4.5-VL-28B-A3B-Thinking
        "defaultVisionModel": "ernie-5.0",
        "supportsVision": True,
        "docUrl": "https://cloud.baidu.com/doc/qianfan/s",
    },
    {
        "id": "hunyuan",
        "name": "腾讯混元（OpenAI 兼容）",
        "protocol": "openai",
        "defaultBaseUrl": "https://api.hunyuan.cloud.tencent.com/v1",
        "defaultChatModel": "hunyuan-turbos-20250904",
        # Hy Vision 2.0：新一代视觉理解快思考模型
        "defaultVisionModel": "hy-vision-2.0",
        "supportsVision": True,
        "docUrl": "https://cloud.tencent.com/document/product/1729",
    },
    {
        "id": "kimi",
        "name": "Moonshot Kimi（OpenAI 兼容）",
        "protocol": "openai",
        "defaultBaseUrl": "https://api.moonshot.cn/v1",
        "defaultChatModel": "kimi-k3",
        # Kimi K3（2026-07-27）2.8T 原生视觉；K2.7-code / K2.6 同样支持视觉
        "defaultVisionModel": "kimi-k3",
        "supportsVision": True,
        "docUrl": "https://platform.moonshot.cn/docs/intro",
    },
    # ---- 本地 / 自部署 ----
    {
        "id": "ollama",
        "name": "Ollama（本地，OpenAI 兼容）",
        "protocol": "openai",
        "defaultBaseUrl": "http://localhost:11434/v1",
        # qwen3.8-flash-next：125B MoE 6B active；视觉走 qwen3-vl / qwen2.5vl
        "defaultChatModel": "qwen3.8-flash-next",
        "defaultVisionModel": "qwen3-vl",
        "supportsVision": True,
        "docUrl": "https://github.com/ollama/ollama/blob/main/docs/openai.md",
    },
    {
        "id": "vllm",
        "name": "vLLM（自托管）",
        "protocol": "openai",
        "defaultBaseUrl": "http://localhost:8000/v1",
        "defaultChatModel": "Qwen/Qwen3-235B-Instruct",
        "defaultVisionModel": "Qwen/Qwen3-VL-72B-Instruct",
        "supportsVision": True,
        "docUrl": "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
    },
    # ---- 自定义（兜底，国外厂商走这里） ----
    {
        "id": "custom",
        "name": "自定义（OpenAI 兼容）",
        "protocol": "openai",
        "defaultBaseUrl": "",
        "defaultChatModel": "",
        "defaultVisionModel": "",
        "supportsVision": True,
        "docUrl": "",
    },
    {
        "id": "custom-anthropic",
        "name": "自定义（Anthropic 原生）",
        "protocol": "anthropic",
        "defaultBaseUrl": "",
        "defaultChatModel": "",
        "defaultVisionModel": "",
        "supportsVision": True,
        "docUrl": "https://docs.anthropic.com/en/api/messages",
    },
    {
        "id": "custom-gemini",
        "name": "自定义（Gemini 原生）",
        "protocol": "gemini",
        "defaultBaseUrl": "",
        "defaultChatModel": "",
        "defaultVisionModel": "",
        "supportsVision": True,
        "docUrl": "https://ai.google.dev/api/generate-content",
    },
]


_PROTOCOL_TO_CLASS: dict[str, type[Provider]] = {
    "openai": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def list_presets() -> list[dict]:
    """返回预置 Provider 元数据列表（深拷贝避免外部修改）。"""
    import copy
    return [copy.deepcopy(p) for p in PRESETS]


def get_preset(preset_id: str) -> dict | None:
    """按 id 查找预置，未命中返回 None。"""
    for p in PRESETS:
        if p.get("id") == preset_id:
            import copy
            return copy.deepcopy(p)
    return None


def build_provider(config: dict) -> Provider:
    """根据 config['protocol'] 实例化对应 Provider。

    未知协议时默认走 OpenAI 兼容（容错来自「自定义」场景）。
    """
    protocol = (config or {}).get("protocol", "openai") or "openai"
    cls = _PROTOCOL_TO_CLASS.get(protocol) or OpenAICompatProvider
    return cls(config or {})


__all__ = [
    "PRESETS",
    "list_presets",
    "get_preset",
    "build_provider",
    "Provider",
    "OpenAICompatProvider",
    "AnthropicProvider",
    "GeminiProvider",
]


# 显式标注 Any 导入防 lint 噪音
_: Any