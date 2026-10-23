#!/usr/bin/env python3
"""Live E2E ops run: upload -> assess -> start -> approve -> real deploy.
Token travels ONLY via LIARA_TOKEN env -> approve body. Never logged."""
import json
import os
import sys
import time

import httpx

BASE = os.getenv("PILOT_BASE", "https://lp-dcded0.liara.run")
ZIP = sys.argv[1] if len(sys.argv) > 1 else "/tmp/e2e-proj.zip"
POLL = int(os.getenv("POLL_S", "6"))
TOKEN = (os.getenv("LIARA_TOKEN") or "").strip()
PLAN_WAIT = int(os.getenv("PLAN_WAIT_S", "180"))
DEPLOY_WAIT = int(os.getenv("DEPLOY_WAIT_S", "900"))

c = httpx.Client(timeout=30.0)


def snap(sid):
    return c.get(f"{BASE}/api/sessions/{sid}").json()


def show_events(s, seen):
    for e in s.get("events", [])[seen:]:
        kind = e.get("type", "?")
        text = e.get("message") or e.get("text") or ""
        if kind in ("stage", "log", "deploy_log", "fix", "report", "approval_required", "error"):
            line = str(text).replace("\n", " ")[:220]
            print(f"  [{kind}] {line}")
    return len(s.get("events", []))


def main() -> int:
    if not TOKEN:
        print("LIARA_TOKEN missing")
        return 2
    with open(ZIP, "rb") as f:
        r = c.post(f"{BASE}/api/upload", files={"file": ("perfume-store.zip", f, "application/zip")})
    r.raise_for_status()
    sid = r.json()["session_id"]
    print("session:", sid)

    r = c.post(f"{BASE}/api/sessions/{sid}/answers", json={"answers": {
        "q1": "solo", "q2": "used", "q3": "know", "q4": "know"},
        "skip_expert": True})
    print("assessment:", r.json())

    r = c.post(f"{BASE}/api/sessions/{sid}/start")
    print("start:", r.status_code, r.json())

    seen = 0
    t0 = time.time()
    status = ""
    while time.time() - t0 < PLAN_WAIT:
        time.sleep(POLL)
        s = snap(sid)
        seen = show_events(s, seen)
        status = s.get("status", "")
        print("  status:", status)
        if status in ("awaiting_approval", "done", "error", "escalated"):
            break
    if status != "awaiting_approval":
        print("FAILED to reach approval gate; status =", status)
        rep = s.get("report") or {}
        print(json.dumps(rep, ensure_ascii=False)[:1500])
        return 1

    plan = s.get("plan") or {}
    print("== PLAN ==")
    print(json.dumps({k: plan.get(k) for k in ("app_name", "platform", "plan", "planID",
                                               "port", "start_command")}, ensure_ascii=False))
    inv = plan.get("invoice") or plan.get("cost") or {}
    print("== INVOICE ==", json.dumps(inv, ensure_ascii=False)[:600])

    r = c.post(f"{BASE}/api/sessions/{sid}/approve", json={
        "approved": True, "cost_ok": True,
        "env_values": {"ADMIN_PASSWORD": "S3cure-Pass-99",
                       "DATABASE_URL": "file:./db/custom.db",
                       "OPENAI_API_KEY": "sk-e2e-test-placeholder-key"},
        "user_reply": "",
        "liara_token": TOKEN,
    })
    print("approve:", r.status_code, r.json())

    t1 = time.time()
    url = ""
    while time.time() - t1 < DEPLOY_WAIT:
        time.sleep(POLL)
        s = snap(sid)
        seen = show_events(s, seen)
        status = s.get("status", "")
        print("  status:", status)
        if status in ("done", "error", "escalated", "awaiting_approval"):
            break
    rep = s.get("report") or {}
    print("== FINAL ==", status)
    print(json.dumps(rep, ensure_ascii=False)[:3000])
    url = (rep.get("url") or (s.get("deploy") or {}).get("url") or "")
    if url:
        print("== VERIFY ==", url)
        for _ in range(12):
            try:
                vr = c.get(url, timeout=15.0)
                print("app http:", vr.status_code, "| bytes:", len(vr.content))
                if vr.status_code < 400:
                    print("E2E DEPLOY REAL ✅")
                    print("TEST_APP:", (plan.get("app_name") or ""))
                    return 0
            except httpx.HTTPError as e:
                print("verify err:", type(e).__name__)
            time.sleep(5)
    return 1 if status != "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
