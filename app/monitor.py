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
import threading
import time
from typing import Any

from agent.risk import safe_action

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
            plan_id = None
            try:
                plan_id = deployer.current_plan()  # live planID (T5) — the only
                # live plan fact: the catalog/prices endpoint does NOT exist
            except Exception:  # noqa: BLE001
                plan_id = None
            return {**base, "source": "liara_api", "label": "UNVERIFIED",
                    "plan_id": plan_id, "metrics": metrics, "probe": probe}

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


# ---------------------------------------------------------------- periodic monitoring (T6)

def _cfg() -> dict:
    from app.config import load_defaults

    m = load_defaults().get("monitor", {})
    return {
        "interval_s": float(os.getenv("MONITOR_INTERVAL_S", str(m.get("interval_s", 30)))),
        "history_n": int(os.getenv("MONITOR_HISTORY_N", str(m.get("history_n", 30)))),
        "alert_after": int(os.getenv("MONITOR_ALERT_AFTER", str(m.get("alert_after", 2)))),
        "max_lifetime": int(m.get("max_lifetime_samples", 600)),
    }


def _sample_healthy(sample: dict) -> bool:
    """A sample is healthy when BOTH the app status says so AND the live URL
    (if any) answered. Any evidence to the contrary counts as unhealthy."""
    if sample.get("source") == "liara_api":
        probe = sample.get("probe") or {}
        return bool(probe.get("ok"))
    if sample.get("healthy") is False:
        return False
    probe = sample.get("probe") or {}
    if probe.get("url") and not probe.get("ok"):
        return False
    return True


class SessionMonitor(threading.Thread):
    """Periodic health poller for one deployed session (read-only by design).

    - polls every `interval_s` (env MONITOR_INTERVAL_S; <=0 disables -> not started)
    - keeps a SHORT history (MONITOR_HISTORY_N) in RAM on the session
    - after MONITOR_ALERT_AFTER consecutive unhealthy samples emits ONE
      `monitor_alert` SSE event per degradation episode, with a SUGGESTED action
      that carries its risk tier (T4) — executing it still goes through the
      tier-2 approval gate in /ops.
    """

    def __init__(self, session) -> None:
        super().__init__(daemon=True, name=f"monitor-{session.id}")
        self.session = session
        self.cfg = _cfg()
        self._halt = threading.Event()
        self._consecutive_bad = 0
        self._alerted_episode = False
        self._count = 0

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        interval = self.cfg["interval_s"]
        if interval <= 0:
            return
        while not self._halt.wait(interval):
            self._count += 1
            if self._count > self.cfg["max_lifetime"]:
                return  # hard stop for long-lived sessions
            try:
                sample = sample_now(self.session)
            except Exception:  # noqa: BLE001 — a broken sample is just a bad sample
                sample = {"ts": round(time.time(), 3), "source": "inference",
                          "label": "استنتاج", "healthy": False, "probe": {}}
            self.record(sample)

    def record(self, sample: dict) -> None:
        """One poll result: history, episode alerting, SSE emit."""
        s = self.session
        healthy = _sample_healthy(sample)
        sample = {**sample, "healthy": healthy}
        s.monitor_history = (s.monitor_history + [sample])[-self.cfg["history_n"]:]
        if healthy:
            self._consecutive_bad = 0
            if self._alerted_episode:
                self._alerted_episode = False  # recovered -> re-arm
                s.monitor_alerting = False
                s.add_event("monitor", {"event": "recovered", "ts": sample.get("ts")})
            return
        self._consecutive_bad += 1
        if self._consecutive_bad >= self.cfg["alert_after"] and not self._alerted_episode:
            self._alerted_episode = True
            s.monitor_alerting = True
            # the suggested action follows the T4 matrix: restart is tier 2,
            # so the UI opens the approval modal before /ops runs it
            s.add_event("monitor_alert", {
                "message_fa": "پایش: سلامت برنامه افت کرده است (نمونه‌های پیاپی ناموفق).",
                "source": sample.get("source"), "label": sample.get("label"),
                "ts": sample.get("ts"),
                "suggested_action": safe_action("restart"),
            })


def start_monitor(session) -> Any | None:
    """Start the periodic monitor for a deployed session (T6). Honors
    MONITOR_INTERVAL_S (<=0 disables). Never starts twice."""
    if session.monitor_thread is not None and session.monitor_thread.is_alive():
        return session.monitor_thread
    if _cfg()["interval_s"] <= 0:
        return None
    t = SessionMonitor(session)
    session.monitor_thread = t
    t.start()
    return t
