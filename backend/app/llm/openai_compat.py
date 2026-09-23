# -*- coding: utf-8 -*-
"""OpenAI 兼容协议实现。

覆盖：OpenAI / DeepSeek / 通义千问 / 智谱 GLM / Moonshot / Ollama / vLLM / 任意自定义 endpoint。

实现细节：
    - 仅依赖 httpx（不引入商业 SDK）
    - chat 直接 POST {baseUrl}/chat/completions
    - vision 把 images 转 base64 data url 嵌入 OpenAI 多模态 content
    - test 用 GET {baseUrl}/models，status<400 即视为成功
    - timeout=60s，使用统一错误处理，所有异常抛出包含 HTTP 状态码的 ProviderError
"""
from __future__ import annotations

import base64
import binascii
from typing import Any, AsyncIterator

import httpx

from .provider import Provider


class ProviderError(RuntimeError):
    """LLM 调用错误。status_code 为 0 表示网络层错误。"""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# 默认 openai 兼容 endpoint（OpenAI 官方）
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_REQUEST_TIMEOUT = 60.0


def _guess_mime(data: bytes) -> str:
    """从字节流嗅探图像 MIME，默认 image/png。"""
    if not data:
        return "image/png"
    head = data[:12]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _image_to_data_url(data: bytes) -> str:
    """bytes → data URL。"""
    mime = _guess_mime(data)
    try:
        b64 = base64.b64encode(data).decode("ascii")
    except (binascii.Error, ValueError) as exc:
        raise ProviderError(f"图片编码失败：{exc}") from exc
    return f"data:{mime};base64,{b64}"


def _normalize_base_url(url: str) -> str:
    """去掉尾随 / 与 /v1，避免用户双重写入。"""
    if not url:
        return _DEFAULT_BASE_URL
    return url.rstrip("/")


class OpenAICompatProvider(Provider):
    """OpenAI 兼容协议（OpenAI / DeepSeek / 通义 / 智谱 / Moonshot / Ollama / vLLM / 自定义）。"""

    name = "openai_compat"
    protocol = "openai"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._base_url = _normalize_base_url(self.config.get("baseUrl", "") or _DEFAULT_BASE_URL)
        self._api_key = self.config.get("apiKey", "") or ""
        self._chat_model = self.config.get("chatModel", "") or "gpt-4o-mini"
        self._vision_model = self.config.get("visionModel", "") or self._chat_model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def chat_model(self) -> str:
        return self._chat_model

    @property
    def vision_model(self) -> str:
        return self._vision_model

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _client(self) -> httpx.AsyncClient:
        # 不在内部共享连接（避免长连接生命周期不可控）
        return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict:
        url = f"{self._base_url}{path}"
        try:
            async with self._client() as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP 请求失败：{exc}") from exc

        if resp.status_code >= 400:
            text = (resp.text or "")[:500]
            raise ProviderError(
                f"HTTP {resp.status_code} {path}：{text}", resp.status_code
            )

        try:
            return resp.json()
        except Exception as exc:
            raise ProviderError(f"响应解析失败：{exc}") from exc

    async def _get(self, path: str) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP 请求失败：{exc}") from exc
        return resp

    # ----- 对外能力 -----

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": model or self._chat_model,
            "messages": messages,
            "temperature": float(temperature),
        }
        data = await self._post_json("/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"响应缺少 choices[0].message.content：{exc}") from exc

    async def vision(
        self,
        images: list[bytes],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        if not images:
            raise ProviderError("vision 调用必须包含至少一张图片")
        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            url = _image_to_data_url(img)
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })
        messages = [{"role": "user", "content": content}]
        return await self.chat(
            messages, model=model or self._vision_model, temperature=0.2
        )

    async def stream_chat(
        self,
        messages: list[dict],
    ) -> AsyncIterator[str]:
        """可选能力：尽量提供最小可用 SSE 流式版本。"""
        payload = {
            "model": self._chat_model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }
        url = f"{self._base_url}/chat/completions"
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", url, headers=self._headers(), json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                        raise ProviderError(
                            f"HTTP {resp.status_code} /chat/completions：{text}",
                            resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            chunk = line[5:].strip()
                            if chunk == "[DONE]":
                                break
                            try:
                                import json
                                obj = json.loads(chunk)
                                delta = (
                                    obj.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content")
                                )
                                if delta:
                                    yield delta
                            except Exception:
                                continue
        except httpx.HTTPError as exc:
            raise ProviderError(f"流式请求失败：{exc}") from exc

    async def test(self) -> tuple[bool, str]:
        """GET /models，status<400 即视为成功。"""
        try:
            resp = await self._get("/models")
        except ProviderError as exc:
            return False, exc.message

        if resp.status_code < 400:
            return True, "ok"

        # 部分兼容端点（如本地 vLLM）可能无 /models；回退发一次最小 chat 请求
        if resp.status_code in (404, 405):
            try:
                await self.chat(
                    [{"role": "user", "content": "hi"}],
                    model=self._chat_model,
                    temperature=0.0,
                )
                return True, "ok（/models 不可用，chat 回退成功）"
            except ProviderError as exc2:
                return False, f"chat 回退失败：{exc2.message}"

        return False, f"HTTP {resp.status_code}：（{(resp.text or '')[:200]}）"
