"""Deployer factory — selection via env DEPLOYER=mock|liara_api|liara_cli (T13).

The user's Liara token (if any) is passed in memory only; it never touches disk,
logs or events. DEMO_MODE=1 forces mock regardless of DEPLOYER (demo must be
fully offline, AGENTS.md).
"""

from __future__ import annotations

import os
from typing import Any


def make_deployer(session: Any = None) -> Any:
    token = getattr(session, "liara_token", None) or os.getenv("LIARA_API_TOKEN", "")
    forced_demo = os.getenv("DEMO_MODE", "") == "1"
    kind = "mock" if forced_demo else os.getenv("DEPLOYER", "mock").strip().lower()

    if kind == "liara_api":
        from deployers.liara_api import LiaraApiDeployer

        return LiaraApiDeployer(api_token=token)
    if kind == "liara_cli":
        from deployers.liara_cli import LiaraCliDeployer

        return LiaraCliDeployer(api_token=token)  # stub: raises until the CLI spike
    from deployers.mock import MockDeployer

    return MockDeployer()
