"""Deployer interface — all real deployment hides behind this (AGENTS.md).

Implementations: MockDeployer (offline/tests/demo), LiaraApiDeployer (Thursday),
LiaraCliDeployer (fallback). No implementation may print or log the user's token.
"""

from __future__ import annotations

from typing import Any, Protocol


class Deployer(Protocol):
    name: str

    def deploy(self, config: dict[str, Any]) -> dict:
        """config: {app_name, platform, port, env, required_env, prepared_files}
        returns {ok, kind, message, url?, logs: [str]} — kind in
        {bad_port, missing_env_var, build_failed, unknown, success}"""
        ...

    def logs(self) -> list[str]:
        """Runtime/application logs for the self-fix loop."""
        ...

    def status(self) -> dict:
        """{healthy, url?} health view used by the verify step."""
        ...


def make_deploy_config(inspection: dict, plan: dict, approvals: dict) -> dict:
    """Pure helper assembling the deploy payload; env VALUES never go into events."""
    env = dict(plan.get("env_placeholders") or {})
    env.update(approvals.get("env_values") or {})
    return {
        "app_name": plan.get("app_name") or "liarapilot-app",
        "platform": inspection.get("platform"),
        "port": plan.get("port"),
        "env": env,
        "required_env": list(inspection.get("env_vars_required") or []),
        "prepared_files": list(plan.get("prepared_files") or []),
    }
