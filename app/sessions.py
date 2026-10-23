"""In-memory session store (one server process, per AGENTS.md 'one session only').

Secrets live here: the SecretRegister and the user's deploy values stay in RAM
and never touch disk or logs. A backend restart loses sessions BY DESIGN; the
API answers 404 {"detail": "session_expired"} and the UI shows a clean message.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    id: str
    project_dir: str
    demo: bool = False
    level: str | None = None
    answers: dict = field(default_factory=dict)
    skip_expert: bool = False
    status: str = "created"          # created|running|awaiting_approval|done|escalated|cancelled|error
    stage_fa: str = ""
    events: list[dict] = field(default_factory=list)
    plan: dict | None = None
    report: dict | None = None
    approval: dict | None = None
    register: Any = None             # SecretRegister
    budget: Any = None               # RunBudget
    llm: Any = None
    deployer: Any = None
    liara_token: str = ""            # user's Liara API token — RAM only, never logged
    checkpointer: Any = None         # graph checkpointer — kept across deployer rebinds
    ask_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)  # 1 LLM call per question (T15)
    graph: Any = None
    thread_cfg: dict = field(default_factory=dict)
    thread: Any = None               # active worker thread (start/resume)
    cancel_flag: bool = False
    error: str = ""
    llm_name: str = "fake"
    # continuous monitoring (T6) — short in-RAM history only, never persisted
    monitor_history: list = field(default_factory=list)
    monitor_alerting: bool = False
    monitor_thread: Any = None
    # care agent (T-care) — نگهبان پروژه: scheduled guardian state, RAM only
    care_schedule: str = ""            # "" | daily | weekly | monthly
    care_next_ts: float = 0.0          # epoch of the next scheduled check
    care_reports: list = field(default_factory=list)   # newest appended, max 20
    care_running: bool = False
    care_brief: str = ""               # cached agent brief (user copyable)
    care_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _seq: int = 0
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_event(self, etype: str, payload: dict) -> dict:
        with self._cond:
            self._seq += 1
            ev = {"i": self._seq, "type": etype, "payload": payload, "ts": round(time.time(), 3)}
            self.events.append(ev)
            self._cond.notify_all()
            return ev

    def wait_for(self, last_i: int, timeout: float) -> bool:
        with self._cond:
            if self._seq > last_i:
                return True
            self._cond.wait(timeout)
            return self._seq > last_i

    def events_after(self, last_i: int) -> list[dict]:
        with self._cond:
            return [e for e in self.events if e["i"] > last_i]

    def seq(self) -> int:
        with self._cond:
            return self._seq

    def terminal(self) -> bool:
        return self.status in ("done", "escalated", "cancelled", "error")

    def acquire_run(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release_run(self) -> None:
        self._lock.release()

    def snapshot(self) -> dict:
        with self._cond:
            return {
                "id": self.id,
                "demo": self.demo,
                "level": self.level,
                "status": self.status,
                "stage_fa": self.stage_fa,
                "events": list(self.events),
                "plan": self.plan,
                "report": self.report,
                "approval": self.approval,
                "deployer": getattr(self.deployer, "name", None),
                "llm_name": self.llm_name,
                "error": self.error,
                "care": self.care_summary(),
            }

    def care_summary(self) -> dict:
        with self._cond:
            return {
                "schedule": self.care_schedule,
                "next_ts": self.care_next_ts,
                "running": self.care_running,
                "reports": list(self.care_reports),
                "has_brief": bool(self.care_brief),
            }


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._glock = threading.Lock()

    def create(self, project_dir: str, demo: bool) -> Session:
        sid = uuid.uuid4().hex[:12]
        s = Session(id=sid, project_dir=project_dir, demo=demo)
        with self._glock:
            self._sessions[sid] = s
        return s

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)


STORE = SessionStore()
