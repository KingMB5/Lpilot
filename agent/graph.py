"""Graph assembly — LangGraph StateGraph with a real human-in-the-loop interrupt.

One compiled graph per session (checkpointer holds the interrupt state).
Nodes are pure functions from agent/nodes.py; edges encode the deterministic
control flow, including the fix-loop cap (max 3, enforced in nodes.fix).
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent import nodes
from agent.nodes import Deps
from agent.state import AgentState


def build_graph(deps: Deps, checkpointer: InMemorySaver | None = None):
    g = StateGraph(AgentState)

    bind = lambda fn: (lambda state: fn(state, deps))  # noqa: E731

    g.add_node("assess_level", bind(nodes.assess_level))
    g.add_node("inspect", bind(nodes.inspect_node))
    g.add_node("explain_risks", bind(nodes.explain_risks))
    g.add_node("make_plan", bind(nodes.make_plan))
    g.add_node("await_approval", bind(nodes.await_approval))
    g.add_node("prepare_files", bind(nodes.prepare_files))
    g.add_node("deploy", bind(nodes.deploy))
    g.add_node("read_logs", bind(nodes.read_logs))
    g.add_node("fix", bind(nodes.fix))
    g.add_node("verify", bind(nodes.verify))
    g.add_node("report", bind(nodes.report))
    g.add_node("teach_summary", bind(nodes.teach_summary))
    g.add_node("escalate", bind(nodes.escalate))
    g.add_node("cancelled", bind(nodes.cancelled))

    g.add_edge(START, "assess_level")
    g.add_edge("assess_level", "inspect")
    g.add_edge("inspect", "explain_risks")
    g.add_edge("explain_risks", "make_plan")
    g.add_edge("make_plan", "await_approval")

    def after_approval(state: dict) -> str:
        if nodes._cancelled(state, deps):
            return "cancelled"
        if state.get("status") == "declined":
            return "cancelled"
        return "prepare_files"

    g.add_conditional_edges("await_approval", after_approval,
                            {"prepare_files": "prepare_files", "cancelled": "cancelled"})

    g.add_edge("prepare_files", "deploy")
    g.add_edge("deploy", "read_logs")

    def after_logs(state: dict) -> str:
        if nodes._cancelled(state, deps):
            return "cancelled"
        if state.get("status") == "deploy_failed":
            return "fix"
        return "verify"

    g.add_conditional_edges("read_logs", after_logs,
                            {"fix": "fix", "verify": "verify", "cancelled": "cancelled"})

    def after_fix(state: dict) -> str:
        if nodes._cancelled(state, deps):
            return "cancelled"
        if state.get("status") == "escalated":
            return "escalate"
        return "deploy"  # retry with the patched plan

    g.add_conditional_edges("fix", after_fix, {"deploy": "deploy", "escalate": "escalate", "cancelled": "cancelled"})

    def after_verify(state: dict) -> str:
        if nodes._cancelled(state, deps):
            return "cancelled"
        if state.get("status") == "deploy_failed":
            return "fix"
        return "report"

    g.add_conditional_edges("verify", after_verify,
                            {"report": "report", "fix": "fix", "cancelled": "cancelled"})

    def after_report(state: dict) -> str:
        if state.get("level") == "beginner" and state.get("status") == "done":
            return "teach_summary"
        return END

    g.add_conditional_edges("report", after_report, {"teach_summary": "teach_summary", END: END})
    g.add_edge("teach_summary", END)
    g.add_edge("escalate", END)
    g.add_edge("cancelled", END)

    return g.compile(checkpointer=checkpointer or InMemorySaver())


def resume(graph, thread_config: dict, approvals: dict) -> dict:
    """Resume an interrupted run with the user's approval payload."""
    return graph.invoke(Command(resume=approvals), thread_config)
