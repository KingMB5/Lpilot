"""MockDeployer — deterministic offline deployment simulator.

Canned behaviours (T7):
- bad_port        : port missing/invalid in deploy config
- missing_env_var : required env vars absent or empty at deploy time
- success         : fake URL + simulated build logs
"""

from __future__ import annotations

from typing import Any


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

        app = f"{self.app_prefix}-{abs(hash(config.get('app_name', 'x'))) % 100000}"
        self._url = f"https://{app}.liara.run"
        self._log(f"start: app listening on port {port}")
        self._log(f"deploy finished. url: {self._url}")
        self._healthy = True
        return {"ok": True, "kind": "success", "message": "deployed", "url": self._url, "logs": list(self._logs)}

    def logs(self) -> list[str]:
        return list(self._logs)

    def status(self) -> dict:
        return {"healthy": self._healthy, "url": self._url}

    def _log(self, line: str) -> None:
        self._logs.append(line)
