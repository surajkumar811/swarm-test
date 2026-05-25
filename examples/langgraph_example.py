"""
LangGraph + swarm-test example.

Demonstrates how to test a LangGraph workflow with SwarmProbe.
This uses a mock StateGraph so it runs without langgraph installed.

Run:
    python examples/langgraph_example.py
    python examples/langgraph_example.py --html
"""

from __future__ import annotations

import argparse

from swarm_test import AgentNode, EventType, InteractionEvent, SwarmProbe


def build_langgraph_swarm():
    """Build a 3-node LangGraph-style workflow as a static graph.

    Topology:
        researcher → analyst → writer
                  ↘ writer  (conditional: if no_analysis_needed)
    """
    researcher = AgentNode(name="researcher", role="researcher")
    analyst = AgentNode(name="analyst", role="analyst")
    writer = AgentNode(name="writer", role="writer")

    events = [
        # researcher → analyst (normal flow)
        InteractionEvent(
            source_agent_id=researcher.id,
            target_agent_id=analyst.id,
            event_type=EventType.TASK_DELEGATE,
            payload={"edge_type": "direct", "step": "research_to_analysis"},
            duration_ms=1200.0,
        ),
        # analyst → writer
        InteractionEvent(
            source_agent_id=analyst.id,
            target_agent_id=writer.id,
            event_type=EventType.TASK_DELEGATE,
            payload={"edge_type": "direct", "step": "analysis_to_write"},
            duration_ms=800.0,
        ),
        # researcher → writer (conditional skip)
        InteractionEvent(
            source_agent_id=researcher.id,
            target_agent_id=writer.id,
            event_type=EventType.TASK_DELEGATE,
            payload={
                "edge_type": "conditional",
                "condition": "no_analysis_needed",
            },
            duration_ms=50.0,
        ),
    ]

    return [researcher, analyst, writer], events


def main() -> None:
    parser = argparse.ArgumentParser(
        description="swarm-test on a LangGraph-style workflow"
    )
    parser.add_argument("--html", action="store_true", help="Export HTML report")
    args = parser.parse_args()

    print("Building 3-node LangGraph workflow...")
    agents, events = build_langgraph_swarm()
    print(f"  agents: {len(agents)}   interactions: {len(events)}")

    print("Initializing SwarmProbe...")
    probe = SwarmProbe(
        swarm_name="langgraph-demo",
        framework="langgraph",
        agents=agents,
        events=events,
    )
    report = probe.run_all()
    report.print_summary()

    if args.html:
        from swarm_test.reporters.html import HtmlReporter

        path = HtmlReporter().render_with_graph(
            report, probe.graph, "langgraph_swarm_report.html"
        )
        print(f"\nHTML report saved: {path}")


if __name__ == "__main__":
    main()
