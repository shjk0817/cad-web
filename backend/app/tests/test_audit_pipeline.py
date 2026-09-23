# -*- coding: utf-8 -*-
"""Task 6 集成测试：end-to-end pipeline + SSE + 单步重跑。

策略：
    1) 用 splitter 直接生成 sheet_1/sheet_2 DXF 文件 + 入 manifest.json，模拟「拆分已经完成」
    2) POST /api/audit/tasks → 阻塞在 awaiting_confirm
    3) GET /api/audit/tasks/<id> → 验证 vlm_candidates 已生成
    4) POST /api/audit/tasks/<id>/confirm-sheets → 跑完 llm_infer + reconcile
    5) GET /api/audit/tasks/<id> → 验证 reconcile_report 有结果
    6) 另一个任务：仅调 /step 重跑 rules 阶段，验证调试模式
    7) GET /api/audit/tasks/<id>/stream → SSE 订阅

用法：
    cd backend && python3 -m app.tests.test_audit_pipeline
"""
from __future__ import annotations

import json
import sys
import time
import tempfile
import threading
import queue as queue_mod
from pathlib import Path

import ezdxf


def _build_workdir(file_id: str) -> Path:
    """在工作目录里模拟一次上传拆分会话。"""
    from app import config as app_cfg
    session_dir = app_cfg.WORK_DIR / file_id
    session_dir.mkdir(parents=True, exist_ok=True)
    # sheet_1：钻孔灌注桩（24 圆 + 标注）
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    for i in range(24):
        msp.add_circle(((i % 6) * 1000, (i // 6) * 1000), radius=400)
    msp.add_text(
        "钻孔灌注桩 N=24 Φ800 长度 L=18m 体积 V=180",
        dxfattribs={"height": 100, "insert": (0, -500)},
    )
    doc.saveas(str(session_dir / "sheet_1.dxf"))
    # sheet_2：承台
    doc = ezdxf.new(dxfversion="R2010")
    doc.encoding = "utf-8"
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (4000, 0), (4000, 1500), (0, 1500)], close=True)
    msp.add_text(
        "承台 CT-3 数量 N=6 厚度 h=1.2m 体积 V=28.8",
        dxfattribs={"height": 100, "insert": (0, -300)},
    )
    doc.saveas(str(session_dir / "sheet_2.dxf"))
    # manifest.json
    with open(session_dir / "manifest.json", "w", encoding="utf-8") as fp:
        json.dump({
            "sheets": [
                {"id": "sheet_1", "name": "钻孔灌注桩布置图"},
                {"id": "sheet_2", "name": "承台 CT-3"},
            ]
        }, fp, ensure_ascii=False)
    return session_dir


def main():
    print("=== Task 6 集成测试：pipeline + SSE + 单步重跑 ===")
    # 用 TestClient + 真实 lifespan
    from fastapi.testclient import TestClient
    from app.main import app
    from app import config as app_cfg

    # 准备 file_id 工作目录
    file_id = "f" * 32
    _build_workdir(file_id)

    with TestClient(app) as client:
        # ---- 1. 创建任务 ----
        print("\n[1] POST /api/audit/tasks")
        r = client.post("/api/audit/tasks", json={
            "fileId": file_id,
            "categories": ["retaining_pile", "bored_pile", "diaphragm_wall", "cap"],
            # 不传 provider：测试无 provider 路径
        })
        assert r.status_code == 200, r.text
        task_a = r.json()["task"]
        task_a_id = task_a["id"]
        print(f"    task.id = {task_a_id}")
        print(f"    stage   = {task_a['stage']}")

        # ---- 2. 等到 awaiting_confirm ----
        for _ in range(60):
            r = client.get(f"/api/audit/tasks/{task_a_id}")
            assert r.status_code == 200
            t = r.json()["task"]
            if t["stage"] == "awaiting_confirm":
                task_a = t
                break
            time.sleep(0.1)
        print(f"\n[2] stage = {task_a['stage']}")
        print(f"    vlmCandidates: {len(t['vlmCandidates'])}")
        assert task_a["stage"] == "awaiting_confirm", task_a
        assert len(task_a["vlmCandidates"]) == 2
        # 无 provider：vlmCandidates 应是 placeholder (score=0.5)
        assert all(c["score"] == 0.5 for c in task_a["vlmCandidates"])

        # ---- 3. confirm-sheets ----
        print("\n[3] POST /api/audit/tasks/<id>/confirm-sheets")
        r = client.post(
            f"/api/audit/tasks/{task_a_id}/confirm-sheets",
            json={"sheetIds": ["sheet_1", "sheet_2"]},
        )
        assert r.status_code == 200, r.text
        t = r.json()["task"]
        print(f"    stage = {t['stage']}")

        # ---- 4. 等待 done ----
        for _ in range(60):
            r = client.get(f"/api/audit/tasks/{task_a_id}")
            t = r.json()["task"]
            if t["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        print(f"\n[4] final stage = {t['stage']} status = {t['status']}")
        assert t["status"] == "done", t
        assert t["stage"] == "done"
        assert "reconcileReport" in t
        rep = t["reconcileReport"]
        print(f"    reconcile.summary = {rep['summary']}")
        print(f"    reconcile.confidence = {rep['confidence']}")
        assert rep["summary"]["totalRows"] > 0
        # 至少有一个 category 命中（cap / bored_pile）
        assert any(rep["byCategory"].get(cat, {}).get("rows") for cat in ("bored_pile", "cap"))
        # 列表（步骤 step_history）
        for st in ["rules", "vlm_select", "awaiting_confirm", "llm_infer", "reconcile", "done"]:
            assert st in t["steps"], f"missing step: {st}"
            print(f"    steps[{st}] = {t['steps'][st]['status']}")

        # ---- 5. 调试模式单步重跑：单独再起一个任务，仅重跑 rules ----
        print("\n[5] 调试模式 /step 重跑 rules")
        file_id2 = "e" * 32
        _build_workdir(file_id2)
        r = client.post("/api/audit/tasks", json={
            "fileId": file_id2,
            "categories": ["bored_pile"],
        })
        assert r.status_code == 200
        task_b = r.json()["task"]
        task_b_id = task_b["id"]
        # 等到 awaiting_confirm
        for _ in range(60):
            r = client.get(f"/api/audit/tasks/{task_b_id}")
            t = r.json()["task"]
            if t["stage"] == "awaiting_confirm":
                break
            time.sleep(0.1)
        # 单步重跑 rules
        r = client.post(f"/api/audit/tasks/{task_b_id}/step", json={"stage": "rules"})
        assert r.status_code == 200, r.text
        # 等到重跑完成（rule_draft 还在）
        time.sleep(0.5)
        r = client.get(f"/api/audit/tasks/{task_b_id}")
        t = r.json()["task"]
        print(f"    stage after re-run rules: {t['stage']}")
        print(f"    steps[rules].status = {t['steps']['rules']['status']}")
        assert t["steps"]["rules"]["status"] == "done"

        # ---- 6. SSE 订阅（仅订阅一个刚启动的任务） ----
        print("\n[6] GET /api/audit/tasks/<id>/stream (SSE)")
        file_id3 = "d" * 32
        _build_workdir(file_id3)
        r = client.post("/api/audit/tasks", json={
            "fileId": file_id3,
            "categories": ["bored_pile"],
        })
        task_c_id = r.json()["task"]["id"]
        sse_chunks: list = []
        sse_done = threading.Event()
        def _reader():
            with client.stream("GET", f"/api/audit/tasks/{task_c_id}/stream") as resp:
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    while True:
                        sep_idx = buf.find("\n\n")
                        if sep_idx < 0:
                            break
                        ev = buf[:sep_idx]
                        buf = buf[sep_idx + 2:]
                        if ev.startswith("data:"):
                            sse_chunks.append(ev)
                            payload = json.loads(ev[len("data: "):].strip())
                            if payload.get("status") in ("done", "error"):
                                sse_done.set()
                                return
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        # 触发 confirm
        time.sleep(1.0)
        for _ in range(30):
            r = client.get(f"/api/audit/tasks/{task_c_id}")
            tt = r.json()["task"]
            if tt["stage"] == "awaiting_confirm":
                break
            time.sleep(0.1)
        client.post(f"/api/audit/tasks/{task_c_id}/confirm-sheets", json={"sheetIds": ["sheet_1"]})
        # 等 SSE 收尾（done/error 或超时 15s）
        sse_done.wait(timeout=15.0)
        t.join(timeout=2.0)
        events = [c for c in sse_chunks if c.startswith("data:")]
        print(f"    received {len(events)} SSE data events")
        assert len(events) >= 1
        last_event = json.loads(events[-1][len("data: "):].strip())
        print(f"    last SSE event stage/status = {last_event['stage']}/{last_event['status']}")
        assert last_event["status"] in ("done", "error")

        # ---- 7. 错误路径：不存在 fileId ----
        print("\n[7] 错误路径：fileId 不存在")
        r = client.post("/api/audit/tasks", json={
            "fileId": "0" * 32,
            "categories": ["cap"],
        })
        assert r.status_code == 404
        print(f"    返回 {r.status_code}（文件不存在）")

        # ---- 8. 错误路径：confirm 时 stage 不对 ----
        print("\n[8] 错误路径：在 running 阶段 confirm")
        r = client.post(f"/api/audit/tasks/{task_c_id}/confirm-sheets",
                       json={"sheetIds": ["sheet_1"]})
        # 上面已经 done/error 了；建一个全新任务并立刻尝试 confirm
        file_id4 = "c" * 32
        _build_workdir(file_id4)
        r = client.post("/api/audit/tasks", json={
            "fileId": file_id4,
            "categories": ["cap"],
        })
        tid = r.json()["task"]["id"]
        # 极快地 confirm，可能 stage 还是 running：按代码逻辑，应 400
        r = client.post(f"/api/audit/tasks/{tid}/confirm-sheets",
                       json={"sheetIds": ["sheet_1"]})
        if r.status_code == 400:
            print(f"    返回 {r.status_code}（stage 不允许 confirm）")
        else:
            print(f"    stage 已切到 awaiting_confirm，confirm 成功 {r.status_code}")

    print("\n=== Task 6 集成测试：全部通过 ===")


if __name__ == "__main__":
    main()