"""Phase-3 T2 — metrics verification wiring + read-only monitor endpoint.

Evidence: config/spec/paas.yaml (official snapshot, checked 2026-10-20),
docs/METRICS_VERIFICATION.md. These tests pin the READ-ONLY contract:
- GET /v1/projects/{name}/metrics/summary (no params) -> summary body
- GET /v1/projects/{name}/logs now SENDS the required `since` param
- /api/sessions/{id}/monitor is read-only, labelled liara_api|inference, and
  never calls a mutating endpoint.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.monitor import has_metrics_support, sample_now
from deployers.liara_api import LiaraApiDeployer

BASE = "https://api.liara.ir"
TOKEN = "liara-token-abc123"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DEMO_MODE", "DEPLOYER", "LIARA_API_TOKEN", "LIARA_DRY_RUN",
                "LIARA_LOGS_SINCE", "MONITOR_HTTP_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)


def deployer(**kw) -> LiaraApiDeployer:
    kw.setdefault("api_token", TOKEN)
    kw.setdefault("backoff_base", 0)
    kw.setdefault("sleep", lambda _s: None)
    d = LiaraApiDeployer(**kw)
    d._app_name = "my-app"  # simulate a completed deploy
    return d


# ---------------------------------------------------------------- deployer

@respx.mock
def test_metrics_summary_returns_body():
    route = respx.get(f"{BASE}/v1/projects/my-app/metrics/summary").mock(
        return_value=httpx.Response(200, json={
            "cpuUsage": [{"value": ["12", 34], "applet": "app"}],
            "memoryUsage": [{"value": ["512Mi"], "applet": "app"}],
            "disksUsage": [{"size": "1G", "usage": "100M", "reportedAt": "t", "name": "database"}],
        }))
    d = deployer()
    out = d.metrics_summary()
    assert route.called
    assert out["disksUsage"][0]["name"] == "database"


@respx.mock
def test_metrics_summary_none_on_error_and_dry_run(monkeypatch):
    respx.get(f"{BASE}/v1/projects/my-app/metrics/summary").mock(
        return_value=httpx.Response(404, json={"error": "not found"}))
    assert deployer().metrics_summary() is None
    # dry_run never performs the read
    assert deployer(dry_run=True).metrics_summary() is None
    # no token -> no call at all
    assert deployer(api_token="").metrics_summary() is None


@respx.mock
def test_logs_sends_required_since_param(monkeypatch):
    route = respx.get(f"{BASE}/v1/projects/my-app/logs").mock(
        return_value=httpx.Response(200, json=[{"type": "app", "datetime": "d", "message": "hi"}]))
    d = deployer()
    lines = d.logs()
    assert route.called
    assert route.calls[0].request.url.params["since"] == "0"  # spec: since REQUIRED
    assert lines[-1].endswith("hi")
    monkeypatch.setenv("LIARA_LOGS_SINCE", "1729400000")
    d2 = deployer()
    d2.logs()
    assert route.calls[1].request.url.params["since"] == "1729400000"


@respx.mock
def test_restart_submitted_and_gated_errors():
    route = respx.post(f"{BASE}/v1/projects/my-app/actions/restart").mock(
        return_value=httpx.Response(200))
    out = deployer().restart()
    assert out["ok"] and route.called
    out401 = deployer()  # fresh app state, route now returns 401
    route.mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
    out401 = out401.restart()
    assert not out401["ok"] and out401["kind"] == "auth_failed"
    # dry-run plans but never sends
    d = deployer(dry_run=True)
    out_dry = d.restart()
    assert out_dry["kind"] == "dry_run"
    assert len([c for c in route.calls]) == 2  # no new POST from the dry run


# ---------------------------------------------------------------- monitor sample

class _FakeSession:
    """Minimal session stand-in for sample_now()."""

    def __init__(self, deployer=None, report=None, status="done", plan=None):
        self.deployer = deployer
        self.report = report or {}
        self.status = status
        self.plan = plan or {}


def test_sample_inference_path_labels_inference(monkeypatch):
    class MockD:
        name = "mock"

        def status(self):
            return {"healthy": True, "url": "https://x.liara.run", "status": "running"}

        def logs(self):
            return ["2026-10-20 app: Out of memory (used 97%)", "2026-10-20 app: started"]

    s = _FakeSession(deployer=MockD(), report={"url": None}, status="done")
    monkeypatch.setattr("app.monitor._url_response_ms", lambda url: {"url": url, "ok": True, "response_ms": 42})
    sample = sample_now(s)
    assert sample["source"] == "inference"
    assert sample["label"] == "استنتاج"
    assert "Out of memory" in sample["suspect_log_lines"][0]
    assert sample["probe"]["response_ms"] == 42


def test_sample_api_path_only_for_liara_api_deployer(monkeypatch):
    class MockD:
        name = "mock"

        def metrics_summary(self):  # would be wrong to call — mock has no API metrics
            raise AssertionError("mock deployer must not be polled for API metrics")

    assert not has_metrics_support(MockD())
    assert has_metrics_support(type("D", (), {"name": "liara-api", "metrics_summary": lambda s: None})())


@respx.mock
def test_monitor_endpoint_readonly_contract():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    sid = c.post("/api/sessions/demo", json={"fixture": "plain-html"}).json()["session_id"]
    resp = c.get(f"/api/sessions/{sid}/monitor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sample"]["source"] in ("liara_api", "inference")
    assert body["sample"]["label"] in ("UNVERIFIED", "استنتاج")
    assert body["history"] == [] and body["alerting"] is False
    assert body["interval_s"] >= 0
    # read-only: no mutation of session status/events
    snap = c.get(f"/api/sessions/{sid}").json()
    assert snap["status"] == "created"
    assert snap["events"] == []


def test_monitor_endpoint_unknown_session():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    resp = c.get("/api/sessions/deadbeef/monitor")
    assert resp.status_code == 404


# ---------------------------------------------------------------- T6: periodic monitor

from app.monitor import SessionMonitor, _sample_healthy, start_monitor  # noqa: E402


class FlakyDown:
    """A deployer whose app keeps failing the probe (degradation episode)."""

    name = "mock"

    def status(self):
        return {"healthy": False, "url": "https://x.liara.run"}

    def logs(self):
        return ["node: exited with code 137 (oom)"]


class Healthy:
    name = "mock"

    def status(self):
        return {"healthy": True, "url": "https://x.liara.run"}

    def logs(self):
        return []


def _session(deployer):
    class S:
        id = "mon-test"
        status = "done"
        plan = {"app_name": "my-app"}
        report = {"url": None}
        monitor_history = []
        monitor_alerting = False
        monitor_thread = None
        events = []

        def add_event(self, t, p):
            self.events.append({"type": t, "payload": p})

    s = S()
    s.deployer = deployer
    return s


def test_sample_healthy_rules():
    assert _sample_healthy({"source": "liara_api", "probe": {"ok": True}}) is True
    assert _sample_healthy({"source": "liara_api", "probe": {"ok": False}}) is False
    assert _sample_healthy({"source": "inference", "healthy": False}) is False
    assert _sample_healthy({"source": "inference", "healthy": True, "probe": {"url": "u", "ok": False}}) is False
    assert _sample_healthy({"source": "inference", "healthy": True, "probe": {}}) is True


def test_alert_after_consecutive_unhealthy_with_tier2_suggestion():
    s = _session(FlakyDown())
    m = SessionMonitor(s)
    m.record({"ts": 1, "source": "inference", "label": "استنتاج", "healthy": False})
    assert s.monitor_alerting is False          # 1st bad sample: no alert yet
    m.record({"ts": 2, "source": "inference", "label": "استنتاج", "healthy": False})
    assert s.monitor_alerting is True           # 2nd consecutive -> alert
    alerts = [e for e in s.events if e["type"] == "monitor_alert"]
    assert len(alerts) == 1                     # ONE alert per episode
    sug = alerts[0]["payload"]["suggested_action"]
    assert sug["action"] == "restart" and sug["tier"] == "approval"  # T4 gate applies
    m.record({"ts": 3, "source": "inference", "label": "استنتاج", "healthy": False})
    assert len([e for e in s.events if e["type"] == "monitor_alert"]) == 1  # still one


def test_recovery_rearms_alerting_and_history_capped():
    s = _session(FlakyDown())
    m = SessionMonitor(s)
    m.record({"ts": 1, "source": "inference", "label": "استنتاج", "healthy": False})
    m.record({"ts": 2, "source": "inference", "label": "استنتاج", "healthy": False})
    s.deployer = Healthy()
    m.record({"ts": 3, "source": "inference", "label": "استنتاج", "healthy": True})
    assert s.monitor_alerting is False
    rec = [e for e in s.events if e["type"] == "monitor" and e["payload"].get("event") == "recovered"]
    assert len(rec) == 1
    for i in range(50):
        m.record({"ts": 100 + i, "source": "inference", "label": "استنتاج", "healthy": True})
    assert len(s.monitor_history) == 30         # capped short history (default)


def test_start_monitor_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setenv("MONITOR_INTERVAL_S", "0")
    s = _session(Healthy())
    assert start_monitor(s) is None             # opt-out honored


def test_start_monitor_runs_and_records(monkeypatch):
    monkeypatch.setenv("MONITOR_INTERVAL_S", "0.05")
    monkeypatch.setenv("MONITOR_HISTORY_N", "5")
    s = _session(Healthy())
    t = start_monitor(s)
    assert t is not None and t.is_alive()
    import time

    time.sleep(0.4)
    t.stop()
    t.join(timeout=2)
    assert not t.is_alive()
    assert 1 <= len(s.monitor_history) <= 5
    assert all(h["healthy"] for h in s.monitor_history)


# ---------------------------------------------------------------- T5: plans

@respx.mock
def test_current_plan_live_read_only_app_planid():
    """T5: the API has NO plan-catalog endpoint (docs/METRICS_VERIFICATION.md).
    The only live plan fact is the deployed app's own planID from the VERIFIED
    GET /v1/projects/{name} response. Prices stay static-fallback (prices.yaml)."""
    route = respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"planID": "g2", "status": "running"}}))
    d = deployer()
    assert d.current_plan() == "g2"
    assert route.called
    # prices.yaml remains all-UNVERIFIED fallback — structural guarantee
    import yaml
    import pathlib
    prices = yaml.safe_load((pathlib.Path(__file__).resolve().parent.parent / "data" / "prices.yaml").read_text(encoding="utf-8"))
    flat = json.dumps(prices)
    assert "UNVERIFIED" in flat
