"""Read-only monitoring samples (T2/T6).

One sample = the best available view of a deployed app, with an explicit source
label so the UI never presents an inference as an API fact:

- source "liara_api": VERIFIED endpoint GET /v1/projects/{name}/metrics/summary
  (config fact `metrics_endpoints`). Value UNITS are UNVERIFIED until the first
  live read (config fact `metrics_values_semantics`) — the UI shows an
  UNVERIFIED badge whenever this source is used.
- source "inference": when the API path is unavailable (mock deployer, fetch
  failure, no token) resource pressure is INFERRED only from existing evidence —
  deployer status, OOM/crash log lines, and live-URL response time. The UI must
  label this «استنتاج» (inference), never as measured metrics.

This module is read-only by construction: it never mutates anything, never
restarts, never calls paid endpoints.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

OOM_RE = re.compile(r"(out of memory|oom|killed|fatal|crash|exit code|exited with)", re.IGNORECASE)

LIMITS_DEFAULTS = {
    "MONITOR_HTTP_TIMEOUT_S": "5",
}


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, LIMITS_DEFAULTS[key]))
    except (KeyError, ValueError):
        return default


def has_metrics_support(deployer: Any) -> bool:
    """True only for deployers that can really read the metrics API."""
    return hasattr(deployer, "metrics_summary") and getattr(deployer, "name", "") == "liara-api"


def _url_response_ms(url: str | None) -> dict:
    """Response time of the live URL — inference evidence, never a metric."""
    if not url:
        return {"url": None, "ok": False, "response_ms": None}
    timeout = _env_float("MONITOR_HTTP_TIMEOUT_S", 5)
    start = time.monotonic()
    try:
        import httpx

        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            resp = c.get(url)
        ms = int((time.monotonic() - start) * 1000)
        return {"url": url, "ok": resp.status_code < 500, "status_code": resp.status_code,
                "response_ms": ms}
    except Exception:  # noqa: BLE001 — any network failure is just a bad sample
        return {"url": url, "ok": False, "response_ms": None}


def sample_now(session) -> dict:
    """Build one read-only monitoring sample for a session."""
    deployer = session.deployer
    report = session.report or {}
    url = report.get("url")
    base = {"ts": round(time.time(), 3), "status": session.status,
            "app_name": (session.plan or {}).get("app_name")}

    # 1) API path (read-only, VERIFIED endpoint; values UNVERIFIED until live check)
    if deployer is not None and has_metrics_support(deployer):
        metrics = deployer.metrics_summary()
        if metrics is not None:
            probe = _url_response_ms(url)
            return {**base, "source": "liara_api", "label": "UNVERIFIED",
                    "metrics": metrics, "probe": probe}

    # 2) Inference path — existing evidence only, explicitly labelled
    status = deployer.status() if deployer is not None else {}
    log_lines = []
    try:
        log_lines = deployer.logs() if deployer is not None else []
    except Exception:  # noqa: BLE001
        log_lines = []
    suspects = [ln for ln in log_lines if OOM_RE.search(ln)][-5:]
    probe = _url_response_ms(url)
    return {**base, "source": "inference", "label": "استنتاج",
            "healthy": bool(status.get("healthy")),
            "app_status": status.get("status", ""),
            "suspect_log_lines": suspects,
            "probe": probe}
