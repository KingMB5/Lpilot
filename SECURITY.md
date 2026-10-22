# SECURITY.md — LiaraPilot

Threat model, secret handling, approval tiers and hard limits for the LiaraPilot
agent (Starcoach Agentic AI Hackathon — Liara challenge).

## 1. Threat model

| Asset | Risk | Mitigation |
|---|---|---|
| Liara API token | Exfiltration via logs, git, uploads, or LLM prompts | Kept **session-only in RAM** (or process env); redacted before any LLM call; never logged; never written to disk; never committed |
| User project zip | Malicious content (zip-slip, path traversal, zip bombs) | `app/zipsafe.py`: size cap, entry-count cap, path normalization, symlink refusal; **uploaded code is inspected, never executed** |
| Operator LLM budget | A public URL burning paid tokens | `LLM_MAX_CALLS` hard cap per client/run + `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_GLOBAL_M` sliding-window limiter (`app/ratelimit.py`) — **must be set on any public deployment** |
| Liara infrastructure | Agent taking destructive actions autonomously | Three-tier action matrix (below); tier-3 is **never callable by the agent** |
| LLM output | Hallucinated or injected commands | Actions restricted to a strict allowlist (`set_env`, `change_port`, `change_start_command`, `add_disk`, `change_platform`, `ask_user`, `give_up`); JSON schema validation with one retry, then deterministic fallback; ≤3 fix attempts |
| Secrets inside uploaded zips | Leaked to the LLM or logs | `app/redact.py` redacts `.env`/keys **before** anything reaches the LLM; secret register strips known secrets from every prompt and log line |

## 2. Secret handling

- `LIARA_API_TOKEN` and `LLM_API_KEY` exist **only** in: process env, session RAM,
  or the in-flight HTTP header. Nothing persists them.
- `.env` is git-ignored; only `.env.example` (no real values) is tracked.
- Logs are metadata-only for LLM calls (`call#N model=… chars=…` — never content).
- Raw API errors shown to users are redacted by the same secret register.
- If a token is ever exposed (e.g. pasted into a shared chat), **rotate it** in the
  Liara panel and create a new one.

## 3. Action tiers (Human-in-the-Loop)

| Tier | Meaning | Examples |
|---|---|---|
| 1 — automatic | Read-only; safe to run without asking | status, logs, metrics/health reads |
| 2 — needs explicit user approval | Mutating; cost or availability impact | deploy new version, restart, change plan/resize, set env / start command, create disk |
| 3 — forbidden for the agent | Destructive; operator-only | delete app, delete disk |

Tier-2 operations render an approval card in the UI (what changes, cost hint,
approve/cancel) and are executed only after an explicit approve call. Tier-3
endpoints are absent from the agent's action surface by construction; `scripts/live_check.py`
never emits them. Results and raw (redacted) errors of every operation are shown
to the user in the run timeline.

## 4. Hard limits

- Zip upload: size + file-count caps (413 on exceed); unsafe archives rejected (400).
- Self-fix loop: max 3 attempts, then honest `give_up` with a report.
- LLM: `LLM_MAX_CALLS` (default 40) billable calls per run; FakeLLM is the offline
  fallback when no key is configured — demo mode is never billed.
- Rate limiter: per-IP and global sliding windows (off by default; required on
  public deployments). `/api/health` stays exempt for platform monitors.
- Monitoring: short capped in-RAM history; no metrics persistence.

## 5. Reporting

Found a security issue? Contact the repository owner (Mehran) privately.
