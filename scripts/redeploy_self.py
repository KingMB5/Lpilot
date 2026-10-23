"""Redeploy LiaraPilot (fixed build) ONTO the existing live app lp-dcded0.

Usage: LIARA_API_TOKEN=... .venv/bin/python scripts/redeploy_self.py
- token stays in RAM (env), never printed/logged
- updates the SAME app (lp-dcded0) -> same public URL preserved
- source zip excludes .venv/fixtures-local/tests artifacts
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deployers.liara_api import LiaraApiDeployer  # noqa: E402

APP = "lp-dcded0"
START = "uvicorn app.main:app --host 0.0.0.0 --port 3000"

d = LiaraApiDeployer()  # token from LIARA_API_TOKEN env; plan defaults small-g2
config = {
    "app_name": APP,
    "platform": "python",
    "port": 3000,
    "env": {"PORT": "3000"},
    "required_env": [],
    "prepared_files": [],
    "drop_files": [],
    "disks": [],
    "start_command": START,
    "project_dir": os.getcwd(),
}
extra = [".venv", "fixtures-local", ".pytest_cache", "tool-results",
         "node_modules", ".next", "upload", "logs", ".zscripts"]
result = d.deploy({**config, "drop_files": extra})
print("ok:", result.get("ok"), "| kind:", result.get("kind"))
print("url:", result.get("url"))
print("message:", str(result.get("message"))[:300])
bad = [l for l in result.get("logs", []) if "error" in l.lower() or "FAILED" in l]
for line in bad[-12:]:
    print("log|", line[:220])
