#!/usr/bin/env python3
"""live_check.py — staged real-API probe against Liara (Phase-3 T7).

Runs the verified PaaS endpoints in RISK ORDER and prints PASS/FAIL/UNVERIFIED
per step with the RAW error, then appends a dated section to
docs/VERIFIED_MATRIX.md so every "verified" claim traces to a real output.

Risk ordering (mirrors agent/risk.py):
  1. auth + read-only (tier 1)     GET /v1/projects
  2. app details + metrics (tier1) GET /v1/projects/{name}, GET .../metrics/summary
  3. logs (tier 1)                 GET /v1/projects/{name}/logs?since=...
  4. create test app (tier 2)      POST /v1/projects            [LIVE only]
  5. env + disk (tier 2)           POST update-envs, POST disks  [LIVE only]
  6. upload + build (tier 2)       POST /v2/projects/{name}/sources [LIVE only]
  7. restart (tier 2)              POST .../actions/restart      [LIVE + explicit approval]
  8. cleanup hint (tier 3)         delete endpoints are NEVER called — manual

Modes:
  default                DRY-RUN: tier-1 read-only steps ARE sent (they cannot
                         mutate anything); every tier-2 step is only PLANNED and
                         printed (payloads redacted) — nothing mutating is sent.
  --live                 tier-2 steps are actually sent (needs LIARA_PLAN_ID).
  --approve-restart      additionally allows the restart step (tier 2, explicit).
  --app NAME             existing app to probe (default: liara-pilot-probe).

No secret is ever printed: the token is redacted in every output line.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import sys
import time
import zipfile
from datetime import datetime, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = os.getenv("LIARA_API_BASE_URL", "https://api.liara.ir").rstrip("/")
MATRIX_DOC = ROOT / "docs" / "VERIFIED_MATRIX.md"
RETRY = (429, 500, 502, 503, 504)


class Step:
    def __init__(self, name: str, tier: int) -> None:
        self.name, self.tier = name, tier
        self.status = "UNVERIFIED"      # PASS | FAIL | UNVERIFIED | SKIPPED
        self.detail = ""
        self.raw = ""                   # raw error / response evidence (redacted)

    def render(self) -> str:
        icon = {"PASS": "✔", "FAIL": "✘", "UNVERIFIED": "?", "SKIPPED": "–"}.get(self.status, "?")
        return f"[{icon} {self.status:<10}] tier{self.tier} {self.name}: {self.detail}"


def client(token: str) -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=20.0,
                        headers={"Authorization": f"Bearer {token}"})


def req(c: httpx.Client, step: Step, method: str, path: str, **kw) -> httpx.Response | None:
    """One request with our 429/5xx backoff; records raw evidence on the step."""
    last = ""
    for attempt in range(1, 4):
        try:
            r = c.request(method, path, **kw)
            if r.status_code in RETRY and attempt < 3:
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            step.raw = f"http {r.status_code} {r.text[:300]}".replace(token_hint(c), "***")
            return r
        except Exception as e:  # noqa: BLE001 — raw error is the evidence
            last = f"{type(e).__name__}: {e}"
            time.sleep(0.5)
    step.raw = last
    return None


def token_hint(c: httpx.Client) -> str:
    return c.headers.get("Authorization", "").split(" ", 1)[-1]


def probe_zip() -> bytes:
    """Minimal deterministic probe app (static platform, no secrets)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("liara.json", json.dumps({"platform": "static", "port": 3000}))
        z.writestr("index.html", "<!doctype html><title>liarapilot probe</title>ok")
    return buf.getvalue()


# ---------------------------------------------------------------- steps (tier 1, read-only)

def step_auth(c, token, app) -> tuple[Step, dict]:
    st = Step("auth+list_apps (GET /v1/projects)", 1)
    r = req(c, st, "GET", "/v1/projects")
    if r is None:
        st.status, st.detail = "FAIL", f"connection failed: {st.raw}"
        return st, {}
    if r.status_code == 401:
        st.status, st.detail = "FAIL", "401 — token rejected (check console API key)"
        return st, {}
    if r.status_code != 200:
        st.status, st.detail = "UNVERIFIED", f"unexpected status {r.status_code}"
        return st, {}
    body = r.json()
    apps = body.get("projects", body) if isinstance(body, dict) else body
    names = [a.get("name") or a.get("project_id") for a in apps][:10] if isinstance(apps, list) else []
    st.status = "PASS"
    st.detail = f"token valid; {len(apps) if isinstance(apps, list) else '?'} apps visible: {names}"
    return st, {"apps": apps if isinstance(apps, list) else []}


def step_app_details(c, token, app) -> Step:
    st = Step(f"app_details (GET /v1/projects/{app})", 1)
    r = req(c, st, "GET", f"/v1/projects/{app}")
    if r is None:
        st.status, st.detail = "FAIL", f"connection failed: {st.raw}"
    elif r.status_code == 404:
        st.status, st.detail = "UNVERIFIED", f"app '{app}' does not exist yet (created in tier-2 steps)"
    elif r.status_code == 200:
        proj = (r.json() or {}).get("project") or {}
        st.status = "PASS"
        st.detail = f"status={proj.get('status')!r} planID={proj.get('planID')!r} platform={proj.get('platform')!r}"
    else:
        st.status, st.detail = "UNVERIFIED", f"unexpected status {r.status_code}"
    return st


def step_metrics(c, token, app) -> Step:
    st = Step(f"metrics_summary (GET /v1/projects/{app}/metrics/summary)", 1)
    r = req(c, st, "GET", f"/v1/projects/{app}/metrics/summary")
    if r is None:
        st.status, st.detail = "FAIL", f"connection failed: {st.raw}"
    elif r.status_code == 404:
        st.status, st.detail = "UNVERIFIED", "app missing OR endpoint path differs live (spec says Reports)"
    elif r.status_code == 200:
        body = r.json() if isinstance(r.json(), dict) else {}
        st.status = "PASS"
        st.detail = ("keys=" + ",".join(sorted(body.keys()))
                     + " — value units still UNVERIFIED until inspected")
    else:
        st.status, st.detail = "UNVERIFIED", f"unexpected status {r.status_code}"
    return st


def step_logs(c, token, app) -> Step:
    st = Step(f"logs (GET /v1/projects/{app}/logs?since=...)", 1)
    since = os.getenv("LIARA_LOGS_SINCE", "0")
    r = req(c, st, "GET", f"/v1/projects/{app}/logs", params={"since": since})
    if r is None:
        st.status, st.detail = "FAIL", f"connection failed: {st.raw}"
    elif r.status_code == 200:
        items = r.json() if isinstance(r.json(), list) else []
        st.status = "PASS"
        st.detail = f"{len(items)} line(s); since={since!r} ACCEPTED (format now VERIFIED-live)"
    elif r.status_code == 400:
        st.status, st.detail = "UNVERIFIED", f"400 with since={since!r} — adjust LIARA_LOGS_SINCE"
    elif r.status_code == 404:
        st.status, st.detail = "UNVERIFIED", f"app '{app}' does not exist yet"
    else:
        st.status, st.detail = "UNVERIFIED", f"unexpected status {r.status_code}"
    return st


# ---------------------------------------------------------------- steps (tier 2, mutating)

def step_create_app(c, token, app, live: bool) -> Step:
    st = Step(f"create_app (POST /v1/projects, name={app})", 2)
    plan_id = os.getenv("LIARA_PLAN_ID", "").strip()
    body = {"name": app, "planID": plan_id or "<LIARA_PLAN_ID>", "platform": "static"}
    if not live:
        st.status = "SKIPPED"
        st.detail = "DRY-RUN plan (not sent): " + json.dumps(body, ensure_ascii=False)
        st.raw = "tier-2 — nothing sent without --live"
        return st
    if not plan_id:
        st.status, st.detail = "UNVERIFIED", "LIARA_PLAN_ID empty — cannot create (values not in spec)"
        return st
    r = req(c, st, "POST", "/v1/projects", json=body)
    if r is None:
        st.status, st.detail = "FAIL", f"connection failed: {st.raw}"
    elif r.status_code in (200, 201):
        st.status, st.detail = "PASS", "app created"
    elif r.status_code == 409:
        st.status, st.detail = "PASS", "409 conflict — app already exists (treated as present)"
    else:
        st.status, st.detail = "FAIL", f"http {r.status_code}"
    return st


def step_env_and_disk(c, token, app, live: bool) -> Step:
    st = Step("env+disk (POST update-envs, POST disks)", 2)
    if not live:
        st.status = "SKIPPED"
        st.detail = ("DRY-RUN plan (not sent): update-envs {project: %s, variables: [{key: PROBE_VAR, value: ***}]}; "
                     "disks {name: probe-disk, size: %s}" % (app, os.getenv("LIARA_DISK_SIZE", "1G")))
        st.raw = "tier-2 — nothing sent without --live"
        return st
    r1 = req(c, st, "POST", "/v1/projects/update-envs",
             json={"project": app, "variables": [{"key": "PROBE_VAR", "value": "liarapilot-live-check"}]})
    if r1 is None or r1.status_code != 200:
        st.status, st.detail = "FAIL", f"update-envs: {st.raw}"
        return st
    r2 = req(c, st, "POST", f"/v1/projects/{app}/disks",
             json={"name": "probe-disk", "size": os.getenv("LIARA_DISK_SIZE", "1G")})
    if r2 is None or r2.status_code not in (200, 201):
        st.status, st.detail = "UNVERIFIED", f"envs ok; disk create: {st.raw} (size units UNVERIFIED)"
        return st
    st.status, st.detail = "PASS", "env + disk accepted"
    return st


def step_upload(c, token, app, live: bool) -> Step:
    st = Step(f"upload+build (POST /v2/projects/{app}/sources)", 2)
    if not live:
        st.status = "SKIPPED"
        st.detail = "DRY-RUN plan (not sent): multipart file=sources.zip (2 entries, static platform)"
        st.raw = "tier-2 — nothing sent without --live"
        return st
    r = req(c, st, "POST", f"/v2/projects/{app}/sources",
            files={"file": ("sources.zip", probe_zip(), "application/zip")})
    if r is None:
        st.status, st.detail = "FAIL", f"connection failed: {st.raw}"
    elif r.status_code == 200:
        st.status, st.detail = "PASS", f"source accepted sourceID={(r.json() or {}).get('sourceID', '?')}"
    else:
        st.status, st.detail = "FAIL", f"http {r.status_code}"
    return st


def step_restart(c, token, app, live: bool, approved: bool) -> Step:
    st = Step(f"restart (POST /v1/projects/{app}/actions/restart)", 2)
    if not live or not approved:
        st.status = "SKIPPED"
        st.detail = "restart is tier-2 — requires --live AND --approve-restart (never implicit)"
        return st
    r = req(c, st, "POST", f"/v1/projects/{app}/actions/restart")
    if r is None:
        st.status, st.detail = "FAIL", f"connection failed: {st.raw}"
    elif r.status_code == 200:
        st.status, st.detail = "PASS", "restart submitted"
    else:
        st.status, st.detail = "FAIL", f"http {r.status_code}"
    return st


# ---------------------------------------------------------------- matrix doc

def write_matrix(results: list[Step], mode: str, app: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "", "---",
        f"## live_check run — {now} (mode: {mode}, app: `{app}`)",
        "",
        "| # | Step (endpoint) | Tier | Verdict | Detail / raw evidence |",
        "|---|-----------------|------|---------|------------------------|",
    ]
    for i, s in enumerate(results, 1):
        detail = (s.detail + (" | raw: " + s.raw if s.raw else "")).replace("|", "\\|")
        lines.append(f"| {i} | {s.name} | {s.tier} | {s.status} | {detail} |")
    lines += ["",
              "_Tier-3 (delete app/disk) is intentionally absent — the agent never calls it._", ""]
    header = None
    if not MATRIX_DOC.exists():
        header = [
            "# VERIFIED MATRIX — live API evidence log",
            "",
            "Every «verified» claim about the LIVE Liara API must trace to a row produced by",
            "`scripts/live_check.py` below (spec-level verification lives in config/defaults.yaml +",
            "docs/METRICS_VERIFICATION.md). PASS = real 200/201 observed; FAIL = real error;",
            "UNVERIFIED = could not confirm; SKIPPED = tier-2 in dry-run mode.",
            "",
        ]
    with MATRIX_DOC.open("a", encoding="utf-8") as f:
        if header:
            f.write("\n".join(header))
        f.write("\n".join(lines))


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Staged real-API probe (dry-run by default)")
    ap.add_argument("--app", default=os.getenv("LIARA_PROBE_APP", "liara-pilot-probe"))
    ap.add_argument("--live", action="store_true", help="actually send tier-2 (mutating) steps")
    ap.add_argument("--approve-restart", action="store_true",
                    help="explicit approval for the tier-2 restart step")
    ap.add_argument("--no-matrix", action="store_true", help="do not append to docs/VERIFIED_MATRIX.md")
    args = ap.parse_args()

    token = os.getenv("LIARA_API_TOKEN", "").strip()
    if not token:
        print("LIARA_API_TOKEN is empty — set it in env (never commit it).", file=sys.stderr)
        return 2

    mode = "LIVE" if args.live else "DRY-RUN"
    print(f"LiaraPilot live_check — {mode} against {BASE} (tier-3 steps are never run)")

    results: list[Step] = []
    with client(token) as c:
        st, info = step_auth(c, token, args.app)
        results.append(st)
        if st.status == "PASS":
            for fn in (lambda: step_app_details(c, token, args.app),
                       lambda: step_metrics(c, token, args.app),
                       lambda: step_logs(c, token, args.app)):
                results.append(fn())
        else:
            skip = Step("(tier-1 steps after auth)", 1)
            skip.status, skip.detail = "SKIPPED", "auth failed — nothing else probed"
            results.append(skip)

        results.append(step_create_app(c, token, args.app, args.live))
        if args.live:
            results.append(step_env_and_disk(c, token, args.app, True))
            results.append(step_upload(c, token, args.app, True))
            results.append(step_app_details(c, token, args.app))  # re-read after build
            results.append(step_restart(c, token, args.app, True, args.approve_restart))
        else:
            for fn in (lambda: step_env_and_disk(c, token, args.app, False),
                       lambda: step_upload(c, token, args.app, False),
                       lambda: step_restart(c, token, args.app, False, False)):
                results.append(fn())

    print()
    for s in results:
        print(s.render())
    ok = all(s.status in ("PASS", "SKIPPED") for s in results)
    print()
    print("summary:", "ALL GREEN (or planned)" if ok else "ATTENTION — see FAIL/UNVERIFIED above")
    if not args.no_matrix:
        write_matrix(results, mode, args.app)
        print(f"matrix updated: {MATRIX_DOC.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
