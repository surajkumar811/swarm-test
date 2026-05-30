"""
ASCII Graph Demo — shows the agent interaction graph in the terminal.

Builds a 5-agent research workflow and renders the topology with
health scores, blast radius, SPOFs, cycles, and critical path.

Run:
    python examples/ascii_graph_demo.py
"""

from __future__ import annotations

from swarm_test import AgentNode, EventType, InteractionEvent, SwarmProbe


def main() -> None:
    # Build a 5-agent workflow with interesting topology
    orch = AgentNode(name="Orchestrator", role="manager")
    researcher = AgentNode(name="Researcher", role="researcher")
    analyst = AgentNode(name="Analyst", role="analyst")
    writer = AgentNode(name="Writer", role="writer")
    reviewer = AgentNode(name="Reviewer", role="reviewer")

    agents = [orch, researcher, analyst, writer, reviewer]

    events = [
        # Main pipeline
        InteractionEvent(
            source_agent_id=orch.id,
            target_agent_id=researcher.id,
            event_type=EventType.TASK_DELEGATE,
            payload={"task": "research topic"},
        ),
        InteractionEvent(
            source_agent_id=researcher.id,
            target_agent_id=analyst.id,
            event_type=EventType.TASK_DELEGATE,
            payload={"task": "analyze findings"},
        ),
        InteractionEvent(
            source_agent_id=analyst.id,
            target_agent_id=writer.id,
            event_type=EventType.TASK_DELEGATE,
            payload={"task": "draft report"},
        ),
        InteractionEvent(
            source_agent_id=writer.id,
            target_agent_id=reviewer.id,
            event_type=EventType.TASK_DELEGATE,
            payload={"task": "review draft"},
        ),
        # Feedback loop: Reviewer -> Orchestrator (creates a cycle)
        InteractionEvent(
            source_agent_id=reviewer.id,
            target_agent_id=orch.id,
            event_type=EventType.AGENT_RESPONSE,
            payload={"result": "review complete"},
        ),
        # Cross-talk: Analyst <-> Researcher (bidirectional)
        InteractionEvent(
            source_agent_id=analyst.id,
            target_agent_id=researcher.id,
            event_type=EventType.CONTEXT_SHARE,
            payload={"data": "analysis feedback"},
        ),
    ]

    probe = SwarmProbe(
        swarm_name="research-crew",
        framework="static",
        agents=agents,
        events=events,
    )

    # Run all tests (computes health scores)
    report = probe.run_all()

    # Print the ASCII graph
    report.print_graph(graph=probe.graph)


if __name__ == "__main__":
    main()
