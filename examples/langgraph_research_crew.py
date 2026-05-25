"""
LangGraph Research Crew — 4-agent workflow with conditional edges.

Topology:
    researcher → analyst → writer → reviewer
                                       ↓
                              approved? ──→ END
                              rejected? ──→ writer  (cycle)

Uses TypedDict shared state and real LangGraph conditional routing.

Run:
    python examples/langgraph_research_crew.py
    python examples/langgraph_research_crew.py --html
    python examples/langgraph_research_crew.py --json
"""

from __future__ import annotations

import argparse
from typing import Any

from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph

from swarm_test import SwarmProbe
from swarm_test.integrations.langgraph_adapter import LangGraphAdapter


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------


class ResearchState(TypedDict):
    topic: str
    research: str
    analysis: str
    draft: str
    feedback: str
    approved: bool
    revision_count: int


# ---------------------------------------------------------------------------
# Node functions (each is an "agent")
# ---------------------------------------------------------------------------


def researcher(state: ResearchState) -> dict[str, Any]:
    """Research node — gathers information on the topic."""
    return {
        "research": f"Research findings on '{state.get('topic', 'AI agents')}': "
        "Multi-agent systems show 3x improvement in task completion when "
        "using structured communication protocols.",
    }


def analyst(state: ResearchState) -> dict[str, Any]:
    """Analyst node — analyses research and extracts key insights."""
    research = state.get("research", "")
    return {
        "analysis": f"Key insight from research ({len(research)} chars): "
        "Structured protocols are the primary driver of performance gains. "
        "Recommendation: implement message schemas between agents.",
    }


def writer(state: ResearchState) -> dict[str, Any]:
    """Writer node — drafts or revises the report."""
    revision = state.get("revision_count", 0)
    analysis = state.get("analysis", "")
    feedback = state.get("feedback", "")

    if revision > 0 and feedback:
        draft = (
            f"[Revision {revision}] Revised report incorporating feedback: "
            f"'{feedback}'. Based on analysis: {analysis[:80]}..."
        )
    else:
        draft = f"[Draft] Report based on analysis: {analysis[:100]}..."

    return {"draft": draft, "revision_count": revision + 1}


def reviewer(state: ResearchState) -> dict[str, Any]:
    """Reviewer node — approves or rejects the draft."""
    revision = state.get("revision_count", 0)
    # Approve on second revision or later
    if revision >= 2:
        return {"approved": True, "feedback": "Approved — good work."}
    return {
        "approved": False,
        "feedback": "Needs more detail on communication protocols.",
    }


def review_router(state: ResearchState) -> str:
    """Route based on reviewer approval."""
    return "approved" if state.get("approved") else "revise"


# ---------------------------------------------------------------------------
# Build the LangGraph
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Construct the 4-node research crew graph."""
    graph = StateGraph(ResearchState)

    graph.add_node("researcher", researcher)
    graph.add_node("analyst", analyst)
    graph.add_node("writer", writer)
    graph.add_node("reviewer", reviewer)

    graph.add_edge(START, "researcher")
    graph.add_edge("researcher", "analyst")
    graph.add_edge("analyst", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_conditional_edges(
        "reviewer",
        review_router,
        {"approved": END, "revise": "writer"},
    )

    return graph


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="swarm-test on a LangGraph research crew"
    )
    parser.add_argument("--html", action="store_true", help="Export HTML report")
    parser.add_argument("--json", action="store_true", help="Export JSON report")
    parser.add_argument(
        "--run-workflow",
        action="store_true",
        help="Actually execute the LangGraph workflow before testing",
    )
    args = parser.parse_args()

    print("Building LangGraph research crew (4 agents)...")
    graph_builder = build_graph()
    compiled = graph_builder.compile()

    # Optionally run the real workflow to see it execute
    if args.run_workflow:
        print("\n--- Executing LangGraph workflow ---")
        result = compiled.invoke({"topic": "Multi-Agent AI Systems", "revision_count": 0})
        print(f"Final draft: {result.get('draft', '')[:200]}")
        print(f"Approved: {result.get('approved')}")
        print(f"Revisions: {result.get('revision_count')}")
        print("--- Workflow complete ---\n")

    # Use the LangGraph adapter to ingest the compiled graph
    print("Ingesting LangGraph into SwarmProbe via adapter...")
    from swarm_test.core.graph import SwarmGraph

    swarm_graph = SwarmGraph()
    adapter = LangGraphAdapter()
    adapter.ingest(compiled, swarm_graph)

    print(f"  agents: {swarm_graph.graph.number_of_nodes()}")
    print(f"  edges: {swarm_graph.graph.number_of_edges()}")

    # Build the probe from the ingested graph
    probe = SwarmProbe(
        swarm_name="langgraph-research-crew",
        framework="langgraph",
        agents=list(swarm_graph.agents.values()),
        events=list(swarm_graph.events),
    )

    print("\nRunning all 6 reliability tests...")
    report = probe.run_all()
    report.print_summary()

    if args.html:
        from swarm_test.reporters.html import HtmlReporter

        path = HtmlReporter().render_with_graph(
            report, probe.graph, "langgraph_research_report.html"
        )
        print(f"\nHTML report saved: {path}")

    if args.json:
        report.to_json("langgraph_research_report.json", graph=probe.graph)
        print("JSON report saved: langgraph_research_report.json")


if __name__ == "__main__":
    main()
