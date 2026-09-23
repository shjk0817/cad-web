# -*- coding: utf-8 -*-
"""Google Gemini 原生协议（generateContent）。

协议参考 https://ai.google.dev/api/generate-content
    POST {baseUrl}/v1beta/models/{model}:generateContent?key={apiKey}
    或带 x-goog-api-key header（推荐同时支持 query 与 header）
"""
from __future__ import annotations

import base64
import json
from typing import Any, AsyncIterator

import httpx

from .provider import Provider
from .openai_compat import ProviderError, _guess_mime


_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
_REQUEST_TIMEOUT = 60.0


def _normalize_base_url(url: str) -> str:
    if not url:
        return _DEFAULT_BASE_URL
    return url.rstrip("/")


class GeminiProvider(Provider):
    name = "gemini"
    protocol = "gemini"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._base_url = _normalize_base_url(self.config.get("baseUrl", "") or _DEFAULT_BASE_URL)
        self._api_key = self.config.get("apiKey", "") or ""
        self._chat_model = self.config.get("chatModel", "") or "gemini-1.5-flash"
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
            h["x-goog-api-key"] = self._api_key
        return h

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    def _contents_from_messages(self, messages: list[dict]) -> tuple[dict | None, list[dict]]:
        """OpenAI messages → Gemini contents + systemInstruction。"""
        system_instruction: dict | None = None
        contents: list[dict] = []
        for msg in messages or []:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = {"parts": [{"text": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}]}
                continue
            # assistant → model
            gemini_role = "model" if role == "assistant" else "user"
            if role not in ("user", "assistant"):
                # 忽略未知 role
                continue
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            contents.append({"role": gemini_role, "parts": [{"text": text}]})
        if not contents:
            contents = [{"role": "user", "parts": [{"text": ""}]}]
        return system_instruction, contents

    async def _post_json(self, model: str, payload: dict[str, Any], stream: bool = False) -> Any:
        action = "streamGenerateContent" if stream else "generateContent"
        url = f"{self._base_url}/v1beta/models/{model}:{action}"
        # streamGenerateContent 允许 alt=sse
        if stream:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}alt=sse"
        try:
            async with self._client() as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP 请求失败：{exc}") from exc
        if resp.status_code >= 400:
            text = (resp.text or "")[:500]
            raise ProviderError(
                f"HTTP {resp.status_code} {action}：{text}", resp.status_code
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
        system_instruction, contents = self._contents_from_messages(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": float(temperature)},
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        data = await self._post_json(model or self._chat_model, payload)
        try:
            candidates = data.get("candidates") or []
            parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
            )
            return "".join((p.get("text", "") for p in parts if isinstance(p, dict)))
        except (IndexError, KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(f"响应缺少 candidates[0].content.parts：{exc}") from exc

    async def vision(
        self,
        images: list[bytes],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        if not images:
            raise ProviderError("vision 调用必须包含至少一张图片")
        parts: list[dict] = []
        for img in images:
            mime = _guess_mime(img)
            try:
                b64 = base64.b64encode(img).decode("ascii")
            except (ValueError, Exception) as exc:
                raise ProviderError(f"图片编码失败：{exc}") from exc
            parts.append({
                "inlineData": {"mimeType": mime, "data": b64},
            })
        parts.append({"text": prompt})
        contents = [{"role": "user", "parts": parts}]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
        }
        data = await self._post_json(model or self._vision_model, payload)
        try:
            candidates = data.get("candidates") or []
            text_parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
            )
            return "".join((p.get("text", "") for p in text_parts if isinstance(p, dict)))
        except (IndexError, KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(f"响应缺少 candidates[0].content.parts：{exc}") from exc

    async def stream_chat(
        self,
        messages: list[dict],
    ) -> AsyncIterator[str]:
        system_instruction, contents = self._contents_from_messages(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        url = f"{self._base_url}/v1beta/models/{self._chat_model}:streamGenerateContent?alt=sse"
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", url, headers=self._headers(), json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                        raise ProviderError(
                            f"HTTP {resp.status_code} streamGenerateContent：{text}",
                            resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            chunk = line[5:].strip()
                            if not chunk or chunk == "[DONE]":
                                continue
                            try:
                                obj = json.loads(chunk)
                                candidates = obj.get("candidates") or []
                                parts = (
                                    candidates[0]
                                    .get("content", {})
                                    .get("parts", [])
                                ) if candidates else []
                                for p in parts:
                                    if isinstance(p, dict) and p.get("text"):
                                        yield p["text"]
                            except Exception:
                                continue
        except httpx.HTTPError as exc:
            raise ProviderError(f"流式请求失败：{exc}") from exc

    async def test(self) -> tuple[bool, str]:
        try:
            await self.chat(
                [{"role": "user", "content": "ping"}],
                model=self._chat_model,
                temperature=0.0,
            )
            return True, "ok"
        except ProviderError as exc:
            return False, exc.message
