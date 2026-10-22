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
