# -*- coding: utf-8 -*-
"""Anthropic 原生协议。

协议文档参考 https://docs.anthropic.com/en/api/messages
    POST {baseUrl}/v1/messages
    Header: x-api-key, anthropic-version: 2023-06-01, content-type: application/json
"""
from __future__ import annotations

import base64
import json
from typing import Any, AsyncIterator

import httpx

from .provider import Provider
from .openai_compat import ProviderError, _guess_mime


_DEFAULT_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_REQUEST_TIMEOUT = 60.0


def _normalize_base_url(url: str) -> str:
    if not url:
        return _DEFAULT_BASE_URL
    return url.rstrip("/")


class AnthropicProvider(Provider):
    name = "anthropic"
    protocol = "anthropic"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._base_url = _normalize_base_url(self.config.get("baseUrl", "") or _DEFAULT_BASE_URL)
        self._api_key = self.config.get("apiKey", "") or ""
        self._chat_model = self.config.get("chatModel", "") or "claude-3-5-sonnet-latest"
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
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    def _messages_to_anthropic(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """OpenAI 风格 messages → Anthropic 风格 system + messages。

        若遇到 system 消息则合并为单一 system prompt（Anthropic 不允许 message.role=system）。
        """
        system_prompt: str | None = None
        converted: list[dict] = []
        for msg in messages or []:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                # 把系统提示合并；若多次出现，使用换行分隔
                if system_prompt is None:
                    system_prompt = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                else:
                    system_prompt += "\n\n" + (
                        content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                    )
            elif role in ("user", "assistant"):
                converted.append({"role": role, "content": content})
            else:
                # 忽略未知 role（如 tool 消息），保持 Anthropic 兼容
                continue
        return system_prompt, converted

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

    # ----- 对外能力 -----

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        system_prompt, converted = self._messages_to_anthropic(messages)
        # Anthropic 要求 messages 至少含 1 条，且首条通常为 user
        if not converted:
            converted = [{"role": "user", "content": ""}]
        payload: dict[str, Any] = {
            "model": model or self._chat_model,
            "messages": converted,
            "temperature": float(temperature),
            "max_tokens": 1024,
        }
        if system_prompt:
            payload["system"] = system_prompt
        data = await self._post_json("/v1/messages", payload)
        try:
            content_blocks = data.get("content") or []
            parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "".join(parts)
        except Exception as exc:
            raise ProviderError(f"响应缺少 content：{exc}") from exc

    async def vision(
        self,
        images: list[bytes],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        if not images:
            raise ProviderError("vision 调用必须包含至少一张图片")
        content: list[dict] = []
        for img in images:
            mime = _guess_mime(img)
            try:
                b64 = base64.b64encode(img).decode("ascii")
            except (ValueError, Exception) as exc:
                raise ProviderError(f"图片编码失败：{exc}") from exc
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": b64,
                },
            })
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]
        return await self.chat(
            messages, model=model or self._vision_model, temperature=0.2
        )

    async def stream_chat(
        self,
        messages: list[dict],
    ) -> AsyncIterator[str]:
        system_prompt, converted = self._messages_to_anthropic(messages)
        if not converted:
            converted = [{"role": "user", "content": ""}]
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "messages": converted,
            "temperature": 0.2,
            "max_tokens": 1024,
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt
        url = f"{self._base_url}/v1/messages"
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", url, headers=self._headers(), json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                        raise ProviderError(
                            f"HTTP {resp.status_code} /v1/messages：{text}",
                            resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        # Anthropic SSE: event: ... \n data: {...}\n
                        if line.startswith("data:"):
                            chunk = line[5:].strip()
                            if not chunk:
                                continue
                            try:
                                obj = json.loads(chunk)
                            except Exception:
                                continue
                            ev = obj.get("type", "")
                            if ev == "content_block_delta":
                                delta = obj.get("delta") or {}
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    if text:
                                        yield text
                            elif ev in ("message_stop",):
                                return
        except httpx.HTTPError as exc:
            raise ProviderError(f"流式请求失败：{exc}") from exc

    async def test(self) -> tuple[bool, str]:
        """用极小的 max_tokens 触发一次最小对话来验证 key/网络。"""
        try:
            payload = {
                "model": self._chat_model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "ping"}],
            }
            await self._post_json("/v1/messages", payload)
            return True, "ok"
        except ProviderError as exc:
            return False, exc.message
