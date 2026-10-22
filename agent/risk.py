"""Three-tier action risk matrix (Phase-3 T4) — the product's permission model.

Every action the product can perform is classified EXACTLY once, here:

  tier 1  auto        (خودکار)        read-only: status, logs, metrics samples.
                                      Runs without asking; cannot mutate anything.
  tier 2  approval    (نیازمند تأیید)  restart, plan resize, env/start-command/port/
                                      platform/disk config changes, new deploys.
                                      Runs ONLY after an explicit user approval for
                                      THAT action — either the graph approval
                                      interrupt (deploy + fix-loop config changes
                                      inside an approved run) or the /ops endpoint
                                      check for post-deploy operations.
  tier 3  forbidden   (فقط دستی)       delete app / delete disk. NEVER offered,
                                      NEVER executed by the agent — mapped to the
                                      real DELETE endpoints in the spec only to
                                      document that we deliberately do not call
                                      them (config fact `destructive_endpoints`).

Enforcement points (all in code, never delegated to the LLM):
- agent/nodes.fix: fix actions are applied only inside a run the user already
  approved, and every fix event carries its tier label for the UI.
- POST /api/sessions/{id}/ops: post-deploy operations re-check the tier — a
  tier-2 action without approved=true is refused with approval_required and
  emits an ops_denied event; tier-3 is always refused.
- The LLM allowlist (llm/diagnose.ALLOWED_ACTIONS) never contains a tier-3
  action, so the model cannot even propose deleting anything.
"""

from __future__ import annotations

TIER_AUTO = "auto"                # سطح ۱
TIER_APPROVAL = "approval"        # سطح ۲
TIER_FORBIDDEN = "forbidden"      # سطح ۳

TIER_FA = {
    TIER_AUTO: "خودکار — فقط خواندنی",
    TIER_APPROVAL: "نیازمند تأیید تو",
    TIER_FORBIDDEN: "فقط دستی — ایجنت هرگز اجرا نمی‌کند",
}

RISK_MATRIX: dict[str, str] = {
    # ---- tier 1: automatic, read-only -------------------------------------
    "read_status": TIER_AUTO,
    "read_logs": TIER_AUTO,
    "read_metrics": TIER_AUTO,
    # ---- tier 2: explicit approval required --------------------------------
    "restart": TIER_APPROVAL,             # POST /v1/projects/{name}/actions/restart
    "resize_plan": TIER_APPROVAL,         # POST /v1/projects/{name}/resize {planID}
    "deploy_new_version": TIER_APPROVAL,  # POST /v2/projects/{name}/sources
    "set_env": TIER_APPROVAL,             # fix-loop config change (inside approved run)
    "change_port": TIER_APPROVAL,
    "change_start_command": TIER_APPROVAL,
    "add_disk": TIER_APPROVAL,            # creates a paid disk
    "change_platform": TIER_APPROVAL,
    # ---- tier 3: never executed by the agent -------------------------------
    "delete_app": TIER_FORBIDDEN,         # DELETE /v1/projects/{name}
    "delete_disk": TIER_FORBIDDEN,        # DELETE /v1/projects/{id}/disks/{name}
}

REASONS_FA = {
    TIER_AUTO: "",
    TIER_APPROVAL: "این اقدام سطح ۲ است و فقط با تأیید صریح تو اجرا می‌شود.",
    TIER_FORBIDDEN: "این اقدام سطح ۳ است؛ ایجنت عمداً هرگز آن را اجرا نمی‌کند (حذف اپ/دیسک فقط دستی).",
}


class RiskDenied(Exception):
    """Raised when an action is not permitted at its risk tier."""

    def __init__(self, action: str, tier: str) -> None:
        self.action, self.tier = action, tier
        super().__init__(f"action '{action}' denied at tier '{tier}'")


def classify(action: str) -> str:
    if action not in RISK_MATRIX:
        # Unknown actions are treated as tier 2 (safest executable assumption)
        # but /ops refuses unknown actions outright — this default exists so a
        # classification bug can never upgrade silently to tier 1.
        return TIER_APPROVAL
    return RISK_MATRIX[action]


def gate(action: str, approved: bool) -> tuple[bool, str]:
    """The single enforcement primitive. Returns (allowed, reason_fa).
    - tier 1: always allowed (read-only by definition).
    - tier 2: allowed only with approved=True for THAT action.
    - tier 3: never allowed, with or without approval."""
    tier = classify(action)
    if tier == TIER_AUTO:
        return True, ""
    if tier == TIER_APPROVAL:
        return (True, "") if approved else (False, REASONS_FA[TIER_APPROVAL])
    return False, REASONS_FA[TIER_FORBIDDEN]


def require(action: str, approved: bool) -> None:
    """gate() as an exception — used where denial must abort the operation."""
    ok, reason = gate(action, approved)
    if not ok:
        raise RiskDenied(action, classify(action))


def safe_action(action: str) -> dict:
    """UI-facing description of an action's risk tier (Persian label included)."""
    tier = classify(action)
    return {"action": action, "tier": tier, "tier_level": {TIER_AUTO: 1, TIER_APPROVAL: 2, TIER_FORBIDDEN: 3}[tier],
            "tier_fa": TIER_FA[tier]}
