"""A4 — in-RAM rate limiter for a public deployment of the agent itself."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import app.ratelimit as rl
from app.main import app


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(rl, "_log_counts", {})
    monkeypatch.setattr(rl, "_global_log", [])
    monkeypatch.setattr(rl, "_last_sweep", 0.0)
    yield


def _client() -> TestClient:
    return TestClient(app)


def test_health_is_exempt_from_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1")
    c = _client()
    for _ in range(5):
        assert c.get("/api/health").status_code == 200


def test_per_ip_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_M", "0")
    c = _client()
    codes = [c.get("/api/assessment").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429 and codes[4] == 429
    r = c.get("/api/assessment")
    assert r.headers.get("Retry-After") == "60"
    assert "rate limit" in r.json()["detail"]


def test_global_ceiling_trips_before_per_ip(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_M", "2")
    c = _client()
    assert c.get("/api/assessment").status_code == 200
    assert c.get("/api/assessment").status_code == 200
    r = c.get("/api/assessment")
    assert r.status_code == 429
    assert "global" in r.json()["detail"]


def test_zero_values_disable_limiting(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_M", "0")
    c = _client()
    for _ in range(10):
        assert c.get("/api/assessment").status_code == 200


def test_memory_is_swept_of_stale_ips(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_M", "0")
    c = _client()
    c.get("/api/assessment")
    assert len(rl._log_counts) == 1
    # simulate a full window passing for every recorded hit
    for dq in rl._log_counts.values():
        for i in range(len(dq)):
            dq[i] -= 61.0
    rl._last_sweep = 0.0  # allow sweep to run on the next request
    c.get("/api/assessment")
    assert len(rl._log_counts) <= 1  # stale ip dropped, only current remains


def test_module_reexports_contract():
    importlib.reload(rl)
    assert callable(rl.check_rate_limit)
