"""Deployer interface — all real deployment hides behind this (AGENTS.md).

Implementations: MockDeployer (offline/tests/demo), LiaraApiDeployer (Thursday),
LiaraCliDeployer (fallback). No implementation may print or log the user's token.
"""

from __future__ import annotations

from typing import Any, Protocol


class Deployer(Protocol):
    name: str

    def deploy(self, config: dict[str, Any]) -> dict:
        """config: {app_name, platform, port, env, required_env, prepared_files,
        project_dir?, start_command?, disks?}
        returns {ok, kind, message, url?, logs: [str], problems?} — kind in
        {bad_port, missing_env_var, wrong_start_script, missing_disk_sqlite,
        build_failed, missing_plan, auth_failed, rate_limited, timeout,
        unknown, success, dry_run, dry_run_failed}"""
        ...

    def logs(self) -> list[str]:
        """Runtime/application logs for the self-fix loop."""
        ...

    def status(self) -> dict:
        """{healthy, url?} health view used by the verify step."""
        ...


def make_deploy_config(inspection: dict, plan: dict, approvals: dict,
                       project_dir: str | None = None) -> dict:
    """Pure helper assembling the deploy payload; env VALUES never go into events.
    Env merge priority: agent fixes (plan.env_fixes) > user approval values > placeholders."""
    env = dict(plan.get("env_placeholders") or {})
    env.update(approvals.get("env_values") or {})
    env.update(plan.get("env_fixes") or {})
    start_command = plan.get("start_command") or inspection.get("start_command")
    return {
        "app_name": plan.get("app_name") or "liarapilot-app",
        "platform": plan.get("platform") or inspection.get("platform"),
        "port": plan.get("port"),
        "env": env,
        "required_env": list(inspection.get("env_vars_required") or []),
        "prepared_files": list(plan.get("prepared_files") or []),
        "disks": list(plan.get("disks") or []),
        "start_command": start_command,
        "project_dir": project_dir,
    }
