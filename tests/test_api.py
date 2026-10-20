"""T8/T9 — API integration: full mock E2E over HTTP (upload->assess->run->approve->done)."""

from __future__ import annotations

import json
import pathlib
import time

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
    raise AssertionError(f"timeout waiting for {status}; last={last.get('status')} err={last.get('error')}")


def test_full_demo_flow_beginner():
    r = client.post("/api/sessions/demo", json={"fixture": "next-prisma-sqlite"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["demo"] is True

    r = client.get("/api/assessment")
    assert len(r.json()["questions"]) == 4

    r = client.post(f"/api/sessions/{sid}/answers",
                    json={"answers": {"q1": "never", "q2": "used", "q3": "know", "q4": "know"}})
    assert r.json()["level"] == "beginner"

    r = client.post(f"/api/sessions/{sid}/start")
    assert r.status_code == 202

    snap = _poll(sid, "awaiting_approval")
    assert snap["approval"]["cost_unverified"] is True
    assert any(f["name"] == "liara.json" for f in snap["approval"]["prepared_files"])

    r = client.post(f"/api/sessions/{sid}/approve",
                    json={"approved": True, "cost_ok": True,
                          "env_values": {"DATABASE_URL": "file:./db/custom.db",
                                          "ADMIN_PASSWORD": "new-strong-pass",
                                          "OPENAI_API_KEY": "sk-test"}})
    assert r.status_code == 202

    snap = _poll(sid, "done")
    assert snap["report"]["status"] == "deployed"
    assert snap["report"]["url"].startswith("https://")
    assert len(snap["report"]["learned"]) == 5   # beginner mode taught

    # SSE replay contains the whole timeline
    r = client.get(f"/api/events/{sid}")
    types = [json.loads(line[6:])["type"] for line in r.text.splitlines() if line.startswith("data: ")]
    assert "risk" in types and "plan" in types and "report" in types and "done" in types


def test_unknown_session_is_clean_404():
    r = client.get("/api/sessions/nonexistent")
    assert r.status_code == 404
    assert r.json()["detail"] == "session_expired"


def test_start_requires_answers():
    r = client.post("/api/sessions/demo", json={"fixture": "node-express"})
    sid = r.json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/start")
    assert r.status_code == 409


def test_answers_validation():
    r = client.post("/api/sessions/demo", json={"fixture": "node-express"})
    sid = r.json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/answers", json={"answers": {"q1": "solo"}})
    assert r.status_code == 400


def test_upload_rejects_non_zip():
    r = client.post("/api/upload", files={"file": ("x.zip", b"this is not a zip", "application/zip")})
    assert r.status_code in (400, 500)


def test_level_toggle():
    r = client.post("/api/sessions/demo", json={"fixture": "node-express"})
    sid = r.json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/level", json={"level": "expert"})
    assert r.json()["level"] == "expert"
    r = client.post(f"/api/sessions/{sid}/level", json={"level": "nope"})
    assert r.status_code == 400


def _poll_any(sid: str, statuses: set[str], timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/sessions/{sid}").json()
        if last["status"] in statuses:
            return last
        time.sleep(0.15)
    raise AssertionError(f"timeout waiting for {statuses}; last={last.get('status')} err={last.get('error')}")


def test_broken_project_escalates_via_api():
    r = client.post("/api/sessions/demo", json={"fixture": "broken-project"})
    sid = r.json()["session_id"]
    client.post(f"/api/sessions/{sid}/answers", json={"skip_expert": True})
    client.post(f"/api/sessions/{sid}/start")
    snap = _poll(sid, "awaiting_approval")
    client.post(f"/api/sessions/{sid}/approve", json={"approved": True, "cost_ok": True, "env_values": {}})
    # T14: the fix loop now ASKS the user each attempt; answer the questions (empty reply)
    for _ in range(6):
        snap = _poll_any(sid, {"awaiting_approval", "escalated"}, timeout=30)
        if snap["status"] == "escalated":
            break
        assert snap["approval"].get("kind") == "question"
        client.post(f"/api/sessions/{sid}/approve", json={"approved": True, "user_reply": ""})
    snap = _poll(sid, "escalated", timeout=30)
    assert snap["report"]["status"] == "needs_user_decision"
    assert snap["report"]["fix_history"][-1]["attempt"] == 3


# ---------------------------------------------------------------- T16: agent self-deploy readiness

def test_health_endpoint_reports_config_state_without_secrets():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["uptime_s"], int)
    assert {"demo_mode", "deployer_default", "llm_configured", "dry_run"} <= set(body)
    assert "token" not in json.dumps(body).lower().replace("liara_token_in_env", "")


def test_agent_liara_json_uses_only_verified_fields():
    spec = json.loads(pathlib.Path(__file__).resolve().parent.parent.joinpath("liara.json").read_text())
    assert spec["platform"] == "python"          # VERIFIED platform value (liara_json_platform_values)
    assert spec["port"] == 3000
    assert set(spec) <= {"platform", "port"}     # nothing unverified shipped


def test_requirements_file_matches_pyproject_pins():
    root = pathlib.Path(__file__).resolve().parent.parent
    reqs = {line.split("==")[0].strip(): line.split("==")[1].strip()
            for line in (root / "requirements.txt").read_text().splitlines()
            if "==" in line and not line.startswith("#")}
    import tomllib
    py = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    pins = {d.split("==")[0]: d.split("==")[1] for d in py if "==" in d}
    assert reqs == pins
