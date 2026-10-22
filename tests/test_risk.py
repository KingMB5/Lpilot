"""Phase-3 T4 — three-tier risk matrix.

The pinning test the operator asked for: a tier-2 action is NEVER executed
without explicit approval, tier-3 is never executed at all, and the LLM
allowlist cannot even propose a tier-3 action.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent.risk import (TIER_APPROVAL, TIER_AUTO, TIER_FORBIDDEN, RISK_MATRIX,
                        RiskDenied, classify, gate, require, safe_action)
from app.main import app
from llm.diagnose import ALLOWED_ACTIONS

client = TestClient(app)


# ---------------------------------------------------------------- unit

def test_matrix_classification():
    assert classify("read_status") == TIER_AUTO
    assert classify("read_logs") == TIER_AUTO
    assert classify("read_metrics") == TIER_AUTO
    for a in ("restart", "resize_plan", "deploy_new_version", "set_env", "change_port",
              "change_start_command", "add_disk", "change_platform"):
        assert classify(a) == TIER_APPROVAL, a
    for a in ("delete_app", "delete_disk"):
        assert classify(a) == TIER_FORBIDDEN, a
    # unknown actions default to the SAFEST executable tier
    assert classify("mystery_op") == TIER_APPROVAL


def test_gate_tier2_never_executes_without_approval():
    ok, reason = gate("restart", approved=False)
    assert not ok and "تأیید" in reason
    with pytest.raises(RiskDenied):
        require("restart", approved=False)
    # the approval is per-action, not a global flag: a tier-2 op still needs it
    for a in ("resize_plan", "set_env", "add_disk"):
        assert gate(a, approved=False) == (False, reason)
    assert gate("restart", approved=True) == (True, "")


def test_gate_tier3_refused_even_with_approval():
    for approved in (False, True):
        ok, reason = gate("delete_app", approved=approved)
        assert not ok and "هرگز" in reason
        with pytest.raises(RiskDenied):
            require("delete_disk", approved=approved)


def test_llm_allowlist_contains_no_tier3_action():
    for a in ALLOWED_ACTIONS:
        assert classify(a) != TIER_FORBIDDEN, f"model must not be able to propose {a}"


def test_safe_action_shape():
    d = safe_action("restart")
    assert d["tier"] == TIER_APPROVAL and d["tier_level"] == 2 and d["tier_fa"]


# ---------------------------------------------------------------- API gate

def _deployed_session() -> str:
    sid = client.post("/api/sessions/demo", json={"fixture": "plain-html"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/answers",
                json={"answers": {"q1": "solo", "q2": "used", "q3": "know", "q4": "know"}})
    client.post(f"/api/sessions/{sid}/start")
    for _ in range(200):
        if client.get(f"/api/sessions/{sid}").json()["status"] == "awaiting_approval":
            break
    client.post(f"/api/sessions/{sid}/approve",
                json={"approved": True, "cost_ok": True, "env_values": {}})
    for _ in range(200):
        if client.get(f"/api/sessions/{sid}").json()["status"] == "done":
            break
    return sid


def test_ops_tier2_without_approval_is_refused_and_visible():
    sid = _deployed_session()
    r = client.post(f"/api/sessions/{sid}/ops", json={"action": "restart"})
    assert r.status_code == 403
    assert r.json()["detail"] == "approval_required"
    events = client.get(f"/api/sessions/{sid}").json()["events"]
    denied = [e for e in events if e["type"] == "ops_denied"]
    assert denied and denied[-1]["payload"]["tier"] == TIER_APPROVAL
    assert "restart" in json.dumps(denied[-1]["payload"], ensure_ascii=False)


def test_ops_tier2_with_approval_executes():
    sid = _deployed_session()
    r = client.post(f"/api/sessions/{sid}/ops", json={"action": "restart", "approved": True})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    events = client.get(f"/api/sessions/{sid}").json()["events"]
    ops = [e for e in events if e["type"] == "ops"]
    assert ops and ops[-1]["payload"]["action"] == "restart"
    assert ops[-1]["payload"]["tier"] == TIER_APPROVAL
    assert ops[-1]["payload"]["ok"] is True
    assert ops[-1]["payload"]["message_fa"] == "restart submitted"  # deployer really ran it


def test_ops_tier3_always_refused_never_reaches_deployer():
    sid = _deployed_session()
    before = len(client.get(f"/api/sessions/{sid}").json()["events"])
    for approved in (False, True):
        r = client.post(f"/api/sessions/{sid}/ops", json={"action": "delete_app", "approved": approved})
        assert r.status_code == 400  # unknown/never-executable from this path
    events = client.get(f"/api/sessions/{sid}").json()["events"]
    assert all(e["type"] != "ops" for e in events)  # nothing executed


def test_ops_unknown_action_refused():
    sid = _deployed_session()
    r = client.post(f"/api/sessions/{sid}/ops", json={"action": "format_everything"})
    assert r.status_code == 400
    r = client.post(f"/api/sessions/{sid}/ops", json={"action": ""})
    assert r.status_code == 400


def test_fix_events_carry_risk_tier_labels():
    sid = _deployed_session_for_broken()
    events = client.get(f"/api/sessions/{sid}").json()["events"]
    fixes = [e for e in events if e["type"] == "fix" and "risk_tier" in e["payload"]]
    assert fixes, "expected at least one config fix with a tier label"
    for e in fixes:
        assert e["payload"]["risk_tier"]["tier"] == TIER_APPROVAL
        assert e["payload"]["risk_note_fa"]


def _deployed_session_for_broken() -> str:
    sid = client.post("/api/sessions/demo", json={"fixture": "broken-project"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/answers",
                json={"answers": {"q1": "solo", "q2": "used", "q3": "know", "q4": "know"}})
    client.post(f"/api/sessions/{sid}/start")
    for _ in range(200):
        if client.get(f"/api/sessions/{sid}").json()["status"] == "awaiting_approval":
            break
    client.post(f"/api/sessions/{sid}/approve",
                json={"approved": True, "cost_ok": True, "env_values": {}})
    for _ in range(300):
        st = client.get(f"/api/sessions/{sid}").json()["status"]
        if st in ("done", "awaiting_approval"):
            break
    # answer a question interrupt if present, then let the run finish
    for _ in range(400):
        st = client.get(f"/api/sessions/{sid}").json()["status"]
        if st == "awaiting_approval":
            client.post(f"/api/sessions/{sid}/approve", json={"user_reply": "test-value-1"})
        elif st in ("done", "escalated", "error"):
            break
    return sid
