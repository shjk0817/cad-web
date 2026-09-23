# -*- coding: utf-8 -*-
"""LLM Provider REST 接口。

端点（全部以 /api/llm/providers 前缀）：
    GET    /presets                       预置 Provider 元数据列表
    GET    /configs                       用户已配置的 Provider 列表（apiKey 脱敏）
    POST   /configs                       新增（基于 presetId 复制默认 + 用户覆写）
    PUT    /configs/{id}                  更新
    DELETE /configs/{id}                  删除
    POST   /configs/{id}/test             连通性自检（success/error）

返回结构遵循 LLMProviderPreset / LLMProviderConfig（见前端 types.ts）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from . import registry, store
from .registry import build_provider


log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm/providers", tags=["llm"])


# 配置返回时对 apiKey 做脱敏，仅保留前 4 位 + '***'
def _redact_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"{value[:4]}***"


def _public_config(record: dict) -> dict:
    """返回给前端的脱敏配置。"""
    return {
        **record,
        "apiKey": _redact_api_key(record.get("apiKey", "")),
        "hasApiKey": bool(record.get("apiKey")),
    }


def _merge_from_preset(preset_id: str, override: dict) -> dict:
    """按 presetId 复制预置默认值，再让用户字段覆盖。"""
    preset = registry.get_preset(preset_id) or {}
    merged = {
        "presetId": preset_id,
        "name": override.get("name") or preset.get("name") or preset_id,
        "protocol": override.get("protocol") or preset.get("protocol") or "openai",
        "baseUrl": override.get("baseUrl") or preset.get("defaultBaseUrl") or "",
        "chatModel": override.get("chatModel") or preset.get("defaultChatModel") or "",
        "visionModel": override.get("visionModel") or preset.get("defaultVisionModel") or "",
        "enabled": bool(override.get("enabled", True)),
    }
    # apiKey 单独保留（不在预置里）
    merged["apiKey"] = override.get("apiKey") or ""
    return merged


# ---- 预置 ----

@router.get("/presets")
def list_presets():
    return {"presets": registry.list_presets()}


# ---- 已配置实例 ----

@router.get("/configs")
def list_configs():
    return {"configs": [_public_config(c) for c in store.load_configs()]}


@router.post("/configs")
def create_config(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    preset_id = body.get("presetId") or body.get("preset_id") or ""
    if not preset_id:
        raise HTTPException(status_code=400, detail="缺少 presetId")
    if registry.get_preset(preset_id) is None:
        raise HTTPException(status_code=400, detail=f"未知 presetId：{preset_id}")
    merged = _merge_from_preset(preset_id, body)
    record = store.upsert(merged)
    return {"config": _public_config(record)}


@router.put("/configs/{config_id}")
def update_config(config_id: str, body: dict):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    existing = store.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    # 保留原 presetId 与 id，避免被改坏
    body = dict(body)
    body["id"] = config_id
    body["presetId"] = body.get("presetId") or existing.get("presetId") or ""
    merged = _merge_from_preset(body["presetId"], body)
    merged["id"] = config_id
    # 若用户没传 apiKey，沿用旧的
    if not merged.get("apiKey") and existing.get("apiKey"):
        merged["apiKey"] = existing["apiKey"]
    record = store.upsert(merged)
    return {"config": _public_config(record)}


@router.delete("/configs/{config_id}")
def delete_config(config_id: str):
    ok = store.delete(config_id)
    if not ok:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"ok": True}


@router.post("/configs/{config_id}/test")
async def test_config(config_id: str):
    record = store.get(config_id)
    if record is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    provider = build_provider(record)
    try:
        ok, msg = await provider.test()
    except Exception as exc:  # pragma: no cover - 防御兜底
        log.exception("Provider test 异常")
        ok, msg = False, f"测试异常：{exc}"
    return {"success": ok, "message": msg}


__all__ = ["router"]