"""MockDeployer — deterministic offline deployment simulator.

Canned behaviours (all config-driven, no randomness):
- bad_port            : port missing/invalid in the deploy config
- missing_env_var     : required env vars absent or empty at deploy time
- wrong_start_script  : start command points to an entry file that does not exist
                        in project_dir (mirrors a real MODULE_NOT_FOUND log; the
                        failure log lists existing sibling entries as a hint)
- missing_disk_sqlite : SQLite DATABASE_URL with no disk mounted (mirrors Liara's
                        documented SQLite-needs-a-disk requirement)
- success             : fake URL + simulated build logs
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

_ENTRY_RE = re.compile(r"^(?:node|bun|deno|python3?)\s+\.?/?(\S+)$")


class MockDeployer:
    name = "mock"

    def __init__(self, app_prefix: str = "mock-app") -> None:
        self.app_prefix = app_prefix
        self._logs: list[str] = []
        self._url: str | None = None
        self._healthy = False

    def deploy(self, config: dict[str, Any]) -> dict:
        self._logs = []
        self._log(f"$ liara deploy --platform {config.get('platform')} --port {config.get('port')}")
        self._log("uploading sources ... ok")

        port = config.get("port")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            self._log("error: invalid or missing port in liara.json")
            return {"ok": False, "kind": "bad_port", "message": f"invalid port: {port!r}", "logs": list(self._logs)}

        self._log("build: installing dependencies ... ok")
        self._log("build: compiling ... ok")

        env = config.get("env") or {}
        required = config.get("required_env") or []
        missing = [k for k in required if not str(env.get(k) or "").strip()]
        if missing:
            self._log(f"error: missing environment variables: {', '.join(missing)}")
            return {"ok": False, "kind": "missing_env_var", "message": "missing env: " + ", ".join(missing), "logs": list(self._logs)}

        if (fail := self._check_start_script(config)) is not None:
            return fail
        if (fail := self._check_sqlite_disk(config)) is not None:
            return fail

        app = f"{self.app_prefix}-{abs(hash(config.get('app_name', 'x'))) % 100000}"
        self._url = f"https://{app}.liara.run"
        self._log(f"start: app listening on port {port}")
        self._log(f"deploy finished. url: {self._url}")
        self._healthy = True
        return {"ok": True, "kind": "success", "message": "deployed", "url": self._url, "logs": list(self._logs)}

    def _check_start_script(self, config: dict) -> dict | None:
        """Fail when the start command references a missing entry file (needs project_dir)."""
        start = str(config.get("start_command") or "").strip()
        project_dir = config.get("project_dir")
        if not start or not project_dir:
            return None
        m = _ENTRY_RE.match(start)
        if not m:
            return None
        entry = m.group(1)
        root = pathlib.Path(project_dir)
        if not root.is_dir() or (root / entry).is_file():
            return None
        ext = pathlib.Path(entry).suffix or ".js"
        siblings = sorted(p.relative_to(root).as_posix() for p in root.glob(f"*{ext}")
                          if p.relative_to(root).as_posix() != entry)
        self._log(f"start: launching '{start}'")
        self._log(f"error: cannot find module '{entry}' (MODULE_NOT_FOUND)")
        if siblings:
            self._log(f"hint: existing root entries: {', '.join(siblings[:5])}")
        return {"ok": False, "kind": "wrong_start_script",
                "message": f"start script references missing file: {entry}", "logs": list(self._logs)}

    def _check_sqlite_disk(self, config: dict) -> dict | None:
        """Fail when SQLite is configured without a mounted disk (Liara documented rule)."""
        env = config.get("env") or {}
        url = str(env.get("DATABASE_URL", ""))
        if not url.startswith(("file:", "sqlite:")):
            return None
        if config.get("disks"):
            return None
        self._log("start: launching app")
        self._log("error: SQLiteError: unable to open database file")
        self._log("hint: SQLite on Liara requires a persistent disk mounted via liara.json disks")
        return {"ok": False, "kind": "missing_disk_sqlite",
                "message": "sqlite DATABASE_URL without a mounted disk", "logs": list(self._logs)}

    def logs(self) -> list[str]:
        return list(self._logs)

    def status(self) -> dict:
        return {"healthy": self._healthy, "url": self._url}

    def _log(self, line: str) -> None:
        self._logs.append(line)
