"""LiaraCliDeployer — STUB (no real calls in Phase 1).

Wraps the official `liara` CLI (npm) in a subprocess. Implemented Thursday after
Mehran's manual spike. Keep every behaviour behind Deployer; parse exit codes,
never scrape prompts.

VERIFIED against docs (checked 2026-10-20, see config/defaults.yaml):
- `liara deploy` accepts: --api-token <v>, --path <v>, --platform <v>, -a/--app <v>,
  -p/--port <v>, --detach, --no-app-logs, --build-arg <v...>, --no-cache,
  -b iran|germany, --team-id <v>, --liara-json <v>
- liara.json "platform" accepts: node, next, laravel, php, python, django, flask,
  dotnet, react, angular, vue, static, docker ("next" is official)
- liara.json disks schema: {"disks":[{"name":"database","mountTo":"db"}]}

TODO verify against official docs/spike before implementing:
1. exit codes and machine-readable failure output of `liara deploy` (UNVERIFIED)
2. non-interactive behaviour when liara.json provides app/platform/port (UNVERIFIED)
3. log access after --detach: prefer HTTP API GET /v1/projects/{name}/logs
   (VERIFIED to exist) over CLI scraping (UNVERIFIED)
4. API token sourcing: env var LIARA_API_TOKEN passed as --api-token; token must
   live only in memory for one session and never be logged (AGENTS.md)
5. build log streaming format when NOT using --detach (needed for SSE relay)
6. whether Node runtime exists in the agent's own container — if the agent itself
   is deployed on Liara without Node, prefer LiaraApiDeployer (pure Python)
"""

from __future__ import annotations


class LiaraCliDeployer:
    name = "liara-cli"

    def __init__(self, api_token: str | None = None) -> None:
        # token kept in memory for one session only; never logged or serialized
        self._api_token = api_token
        raise NotImplementedError("implemented Thursday after the manual CLI spike")

    def deploy(self, config: dict) -> dict:  # pragma: no cover
        raise NotImplementedError

    def logs(self) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def status(self) -> dict:  # pragma: no cover
        raise NotImplementedError
