"""Minimal in-RAM rate limiter for a publicly deployed LiaraPilot (A4).

Why: a public agent URL must not be able to burn the operator's LLM budget or
hammer the Liara API. This is a per-IP sliding-window limiter with a global
ceiling, kept deliberately dependency-free and RAM-capped so it can never grow
unbounded. Deployer/token stays session-scoped regardless (defense in depth).

Env:
    RATE_LIMIT_PER_MIN   max requests per client IP per minute (0 disables; DEFAULT 0 = off.
                         A public deployment MUST set this, e.g. 60)
    RATE_LIMIT_GLOBAL_M  max requests per minute process-wide (0 disables; default 0 = off)
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from starlette.responses import JSONResponse

_log_counts: dict[str, deque[float]] = defaultdict(deque)
_global_log: deque[float] = deque()
_lock = threading.Lock()
_last_sweep = 0.0

_EXEMPT_PATHS = ("/api/health",)


def _client_ip(scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def _sweep(now: float, window: float) -> None:
    """Drop stale entries so memory stays bounded (called at most 1/s)."""
    global _last_sweep
    if now - _last_sweep < 1.0:
        return
    _last_sweep = now
    stale = [k for k, v in _log_counts.items() if not v or v[-1] < now - window]
    for k in stale:
        _log_counts.pop(k, None)


def check_rate_limit(scope) -> JSONResponse | None:
    """Return a 429 JSONResponse when a limit is exceeded, else None."""
    per_min = int(os.getenv("RATE_LIMIT_PER_MIN", "0"))  # disabled by default; set on public deploys
    global_m = int(os.getenv("RATE_LIMIT_GLOBAL_M", "0"))  # disabled by default; set on public deploys
    if per_min <= 0 and global_m <= 0:
        return None

    path = scope.get("path", "")
    if any(path.startswith(p) for p in _EXEMPT_PATHS):
        return None  # healthcheck stays reachable for the platform

    now = time.monotonic()
    window = 60.0
    with _lock:
        _sweep(now, window)

        if global_m > 0:
            g = _global_log
            while g and g[0] < now - window:
                g.popleft()
            if len(g) >= global_m:
                return JSONResponse(
                    {"detail": "global rate limit exceeded — try again later"},
                    status_code=429, headers={"Retry-After": "60"})
            g.append(now)

        if per_min > 0:
            key = _client_ip(scope)
            dq = _log_counts.setdefault(key, deque())
            while dq and dq[0] < now - window:
                dq.popleft()
            if len(dq) >= per_min:
                return JSONResponse(
                    {"detail": "rate limit exceeded — max "
                               f"{per_min} requests/minute"},
                    status_code=429, headers={"Retry-After": "60"})
            dq.append(now)
    return None
