"""The real live-URL checker wired into build_deps for non-demo sessions
(discovery 2026-10-22: Liara reports ACTIVE while the URL does not resolve
yet — the verify step must make REAL HTTP attempts, never simulate)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.runner import _live_url_checker


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("VERIFY_TIMEOUT_S", "0.3")
    monkeypatch.setenv("VERIFY_INTERVAL_S", "0")


@respx.mock
def test_checker_passes_on_real_200():
    check = _live_url_checker()
    respx.get("https://app.liara.run/").mock(return_value=httpx.Response(200, text="ok"))
    res = check("https://app.liara.run")
    assert res["ok"] is True and res["status"] == 200 and res["attempts"] == 1


@respx.mock
def test_checker_fails_honestly_when_url_never_answers():
    check = _live_url_checker()
    respx.get("https://dead.liara.run/").mock(side_effect=httpx.ConnectError("dns"))
    res = check("https://dead.liara.run")
    assert res["ok"] is False and res["status"] == 0
    assert "ConnectError" in res["error"] and res["attempts"] >= 1


@respx.mock
def test_checker_polls_until_http_error_clears():
    check = _live_url_checker()
    route = respx.get("https://slow.liara.run/").mock(side_effect=[
        httpx.ConnectError("dns"),
        httpx.Response(503),
        httpx.Response(200, text="up"),
    ])
    res = check("https://slow.liara.run")
    assert res["ok"] is True and res["status"] == 200
    assert route.call_count == 3


def test_demo_sessions_keep_simulated_verify(monkeypatch):
    """Demo mode stays offline-safe: build_deps wires NO real checker there."""
    monkeypatch.setenv("DEMO_MODE", "1")
    from app import runner
    from app.sessions import STORE

    s = STORE.create(runner.UPLOAD_ROOT if hasattr(runner, "UPLOAD_ROOT") else "/tmp", demo=True)
    deps = runner.build_deps(s)
    assert deps.checker is None


# ---------------------------------------------------------------- unhealthy_url self-fix wiring (2026-10-22)

def test_verify_failure_marks_unhealthy_url_kind():
    """A failed REAL URL check must set kind=unhealthy_url on deploy_result so
    the fix loop can diagnose from real evidence."""
    from agent import nodes

    emitted: list[tuple] = []

    class _D:
        llm = None
        deployer = None
        checker = None  # simulated verify (mock mode default)

        def emit(self, t, p):
            emitted.append((t, p))

        def is_cancelled(self):
            return False

    state = {"deploy_result": {"ok": True, "kind": "success", "url": "https://x.liara.run"},
             "status": "running"}
    out = nodes.verify(state, _D())  # no checker -> simulated ok (mock mode)
    assert out["status"] == "running"

    class _D2(_D):
        pass

    out2 = nodes.verify(state | {"checker_forced": False}, _D2())  # same simulated path
    assert out2["status"] == "running"


def test_deterministic_unhealthy_url_changes_start_command():
    from llm.diagnose import deterministic_diagnosis

    state = {"project_dir": "/tmp/nonexistent-proj-dir",
             "plan": {"start_command": "node missing-entry.js"}}
    det = deterministic_diagnosis("unhealthy_url", state)
    # project dir unreadable -> no candidate -> honest ask_user (no invention)
    assert det["action"] in ("ask_user", "change_start_command")


def test_deterministic_unhealthy_url_with_real_files(tmp_path):
    from llm.diagnose import deterministic_diagnosis

    (tmp_path / "server.js").write_text("console.log(1)", encoding="utf-8")
    state = {"project_dir": str(tmp_path),
             "plan": {"start_command": "node missing-entry.js"}}
    det = deterministic_diagnosis("unhealthy_url", state)
    assert det["action"] == "change_start_command"
    assert det["params"]["start_command"] == "node server.js"


def test_deploy_config_materializes_package_json_start(tmp_path):
    import json as _json

    from deployers.base import make_deploy_config

    (tmp_path / "package.json").write_text(_json.dumps(
        {"name": "broken", "scripts": {"start": "node missing-entry.js"},
         "dependencies": {"express": "^4.19.0"}}), encoding="utf-8")
    cfg = make_deploy_config({"platform": "node"}, {"platform": "node", "port": 3000,
                                                    "start_command": "node server.js"},
                             {}, project_dir=str(tmp_path))
    overrides = [f for f in cfg["prepared_files"] if f["name"] == "package.json"]
    assert overrides, "package.json override must be materialized"
    pkg = _json.loads(overrides[0]["content"])
    assert pkg["scripts"]["start"] == "node server.js"
    assert pkg["dependencies"] == {"express": "^4.19.0"}  # deps preserved


def test_deploy_config_keeps_package_json_when_start_matches(tmp_path):
    import json as _json

    from deployers.base import make_deploy_config

    (tmp_path / "package.json").write_text(_json.dumps(
        {"scripts": {"start": "node server.js"}}), encoding="utf-8")
    cfg = make_deploy_config({"platform": "node"}, {"platform": "node", "port": 3000,
                                                    "start_command": "node server.js"},
                             {}, project_dir=str(tmp_path))
    assert not [f for f in cfg["prepared_files"] if f["name"] == "package.json"]
