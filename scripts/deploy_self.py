"""Redeploy LiaraPilot (this codebase) onto Liara as app lp-dcded0.

Usage: LIARA_API_TOKEN=<token> uv run python scripts/deploy_self.py [--new-app NAME]

Uses the product's own real deployer (LiaraApiDeployer) — no mocks.
The start command travels in the release body args (VERIFIED 2026-10-24:
the python buildpack ignores in-archive liara.json / Procfile).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deployers.liara_api import LiaraApiDeployer  # noqa: E402

START_COMMAND = "uvicorn app.main:app --host 0.0.0.0 --port 3000"
PORT = 3000
PLATFORM = "python"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    token = (os.getenv("LIARA_API_TOKEN") or "").strip()
    if not token:
        print("LIARA_API_TOKEN missing — paste the token as env, never in code.")
        return 2
    app_name = "lp-dcded0"
    for i, a in enumerate(sys.argv):
        if a == "--new-app" and i + 1 < len(sys.argv):
            app_name = sys.argv[i + 1]

    deployer = LiaraApiDeployer(api_token=token)
    config = {
        "app_name": app_name,
        "platform": PLATFORM,
        "port": PORT,
        "env": {"DEMO_MODE": "0", "DEPLOYER": "liara_api"},
        "required_env": [],
        "prepared_files": [],
        # keep the release artifact clean: runtime code + assets only
        "drop_files": [".venv", "tests", "uv.lock", "pyproject.toml", "worklog.md", "db",
                       "fixtures-local", ".pytest_cache", "__pycache__",
                       "skills", "src", "bun.lock", "upload", "tool-results",
                       "data/uploads"],
        "disks": [],
        "start_command": START_COMMAND,
        "project_dir": PROJECT_DIR,
    }
    # env: minimal touch — the live app already carries its LLM config;
    # update-envs only upserts these keys.
    config["env"] = {"DEMO_MODE": "0", "DEPLOYER": "liara_api"}
    if os.getenv("SELF_LLM_API_KEY"):
        config["env"]["LLM_API_KEY"] = os.getenv("SELF_LLM_API_KEY")
    print(f"deploying {PROJECT_DIR} -> app {app_name} (platform={PLATFORM}, port={PORT})")
    result = deployer.deploy(config)
    ok = bool(result.get("ok"))
    print("ok:", ok, "| kind:", result.get("kind"))
    print("message:", result.get("message"))
    print("url:", result.get("url"))
    logs = result.get("logs") or []
    print("--- last deploy log lines ---")
    for line in logs[-25:]:
        print(" ", line)
    if ok and result.get("url"):
        import time

        import httpx

        print("verifying live URL ...")
        for attempt in range(30):
            try:
                r = httpx.get(result["url"] + "/api/health", timeout=10, follow_redirects=True)
                print("verify:", r.status_code, r.text[:160])
                if r.status_code < 400:
                    print("SELF-DEPLOY READY ✅")
                    return 0
            except httpx.HTTPError as e:
                print("verify attempt", attempt + 1, type(e).__name__)
            time.sleep(5)
        print("URL did not answer in time — check the logs above honestly.")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
