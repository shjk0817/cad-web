# -*- coding: utf-8 -*-
"""Provider 配置落盘：JSON 到 backend/tmp/llm_providers.json。

字段约定（每个 config）：
    id          str        uuid4 hex
    presetId    str        预置 Provider id
    name        str        用户命名
    protocol    str        'openai' | 'anthropic' | 'gemini'
    baseUrl     str        自定义 baseUrl
    apiKey      str        密钥
    chatModel   str        默认 chat 模型
    visionModel str        默认 vision 模型
    enabled     bool       是否启用
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


# 落盘路径：backend/tmp/llm_providers.json（与配置 work_dir 解耦，自有 tmp 目录）
_STORE_PATH = Path(__file__).resolve().parent.parent.parent / "tmp" / "llm_providers.json"


def _ensure_dir(path: Path) -> None:
    """确保父目录存在（不存在则创建）。"""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, data: str) -> None:
    """原子写：写入 .tmp 后 os.replace，避免半文件。"""
    _ensure_dir(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        fp.write(data)
    os.replace(tmp, path)


def _normalize(record: dict) -> dict:
    """对单条 config 做字段归一化，确保落盘与 API 返回一致。"""
    out = {
        "id": str(record.get("id") or uuid.uuid4().hex),
        "presetId": str(record.get("presetId") or ""),
        "name": str(record.get("name") or ""),
        "protocol": str(record.get("protocol") or "openai"),
        "baseUrl": str(record.get("baseUrl") or ""),
        "apiKey": str(record.get("apiKey") or ""),
        "chatModel": str(record.get("chatModel") or ""),
        "visionModel": str(record.get("visionModel") or ""),
        "enabled": bool(record.get("enabled", True)),
    }
    return out


def load_configs() -> list[dict]:
    """读取配置列表。文件不存在或解析失败时返回空列表。"""
    if not _STORE_PATH.is_file():
        return []
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, list):
            return []
        return [_normalize(item) for item in data if isinstance(item, dict)]
    except Exception:
        return []


def save_configs(configs: list[dict]) -> None:
    """整列表写盘。"""
    normalized = [_normalize(c) for c in (configs or []) if isinstance(c, dict)]
    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    _atomic_write(_STORE_PATH, payload)


def upsert(config: dict) -> dict:
    """按 id 插入或更新。无 id 时分配新 uuid4。返回归一化后的 record。

    重要：仅在读、改、写整表过程中持有一次性快照，避免半写。
    """
    records = load_configs()
    record = _normalize(config or {})

    target_id = record["id"]
    found = False
    for idx, item in enumerate(records):
        if item.get("id") == target_id:
            records[idx] = record
            found = True
            break
    if not found:
        records.append(record)

    save_configs(records)
    return record


def delete(config_id: str) -> bool:
    """按 id 删除。返回是否真的删了。"""
    records = load_configs()
    new_records = [r for r in records if r.get("id") != config_id]
    deleted = len(new_records) != len(records)
    if deleted:
        save_configs(new_records)
    return deleted


def get(config_id: str) -> dict | None:
    """按 id 取单条，未命中返回 None。"""
    for r in load_configs():
        if r.get("id") == config_id:
            return r
    return None


def store_path() -> Path:
    """暴露当前落盘路径，便于测试或调试。"""
    return _STORE_PATH


__all__ = [
    "load_configs",
    "save_configs",
    "upsert",
    "delete",
    "get",
    "store_path",
]
