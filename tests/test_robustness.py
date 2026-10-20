"""T11 — robustness: SSE Last-Event-ID replay, cancel, restart message, demo flag."""

from __future__ import annotations

import io
import json
import time
import zipfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _poll(sid: str, status: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/sessions/{sid}").json()
        if last["status"] == status:
            return last
        time.sleep(0.15)
    raise AssertionError(f"timeout waiting for {status}; last={last.get('status')}")


def _demo_sid(fixture: str = "node-express") -> str:
    return client.post("/api/sessions/demo", json={"fixture": fixture}).json()["session_id"]


def test_sse_last_event_id_replay():
    sid = _demo_sid()
    client.post(f"/api/sessions/{sid}/answers", json={"skip_expert": True})
    client.post(f"/api/sessions/{sid}/start")
    _poll(sid, "awaiting_approval")
    client.post(f"/api/sessions/{sid}/approve",
                json={"approved": True, "cost_ok": True, "env_values": {"PORT": "3000"}})
    _poll(sid, "done")

    full = client.get(f"/api/events/{sid}").text
    ids = [int(l[4:]) for l in full.splitlines() if l.startswith("id: ")]
    assert ids and ids == sorted(ids)

    cut = ids[len(ids) // 2]
    partial = client.get(f"/api/events/{sid}", headers={"last-event-id": str(cut)}).text
    partial_ids = [int(l[4:]) for l in partial.splitlines() if l.startswith("id: ")]
    assert partial_ids and min(partial_ids) > cut          # replay resumes AFTER the cut
    assert "data: " in partial                              # but still delivers the tail


def test_cancel_while_awaiting_approval():
    sid = _demo_sid("next-prisma-sqlite")
    client.post(f"/api/sessions/{sid}/answers", json={"skip_expert": True})
    client.post(f"/api/sessions/{sid}/start")
    _poll(sid, "awaiting_approval")
    r = client.post(f"/api/sessions/{sid}/cancel")
    assert r.status_code == 202
    snap = client.get(f"/api/sessions/{sid}").json()
    assert snap["status"] == "cancelled"
    # approval after cancel must be refused cleanly
    r = client.post(f"/api/sessions/{sid}/approve", json={"approved": True, "cost_ok": True, "env_values": {}})
    assert r.status_code == 409


def test_backend_restart_message_is_clean():
    r = client.get("/api/sessions/deadbeef00")
    assert r.status_code == 404
    assert r.json()["detail"] == "session_expired"
    # SSE endpoint also answers with the same clean contract
    r = client.get("/api/events/deadbeef00")
    assert r.status_code == 404
    assert r.json()["detail"] == "session_expired"


def test_upload_with_demo_mode_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("package.json", json.dumps({"name": "z", "scripts": {"start": "node s.js"}}))
        zf.writestr("s.js", "listen(3000)")
    r = client.post("/api/upload", files={"file": ("proj.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["demo"] is True
    client.post(f"/api/sessions/{sid}/answers", json={"skip_expert": True})
    client.post(f"/api/sessions/{sid}/start")
    snap = _poll(sid, "awaiting_approval")
    assert snap["llm_name"].startswith("fake")     # DEMO_MODE forced offline LLM
    client.post(f"/api/sessions/{sid}/approve", json={"approved": True, "cost_ok": True, "env_values": {}})
    snap = _poll(sid, "done")
    assert snap["deployer"] == "mock"
