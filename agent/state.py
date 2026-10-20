"""Agent state schema (LangGraph TypedDict) — JSON-able at all times."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    session_id: str
    level: str                 # beginner | expert
    answers: dict[str, str]
    skip_expert: bool
    project_dir: str
    inspection: dict
    explanations: dict[str, dict]   # risk_id -> {beginner_text, expert_text}
    plan: dict
    approvals: dict            # {approved, cost_ok, env_values}
    deploy_result: dict
    logs: list[str]
    fix_attempts: int
    fix_history: list[dict]
    escalation_reason: str     # give_up / cap reason surfaced to the escalate node
    decisions: list[dict]      # audit log: every deterministic decision
    report: dict
    learned: list[str]
    next_time: list[str]
    status: str
    error: str
    cancel_requested: bool
