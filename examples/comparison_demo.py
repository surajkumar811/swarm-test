"""
Comparison Demo — shows before/after reliability improvement.

Builds two versions of a 5-agent pipeline:
  BEFORE: SPOF orchestrator, collusion clique, no fallbacks
  AFTER:  backup orchestrator, broken clique, redundant paths

Runs SwarmProbe on both, saves JSON, and prints the comparison diff.

Run:
    python examples/comparison_demo.py
"""

from __future__ import annotations

from swarm_test import AgentNode, EventType, InteractionEvent, SwarmProbe
from swarm_test.comparison import ReportComparator


# ---------------------------------------------------------------------------
# BEFORE: problematic architecture
# ---------------------------------------------------------------------------


def build_before() -> SwarmProbe:
    """
    5-agent workflow with known reliability problems:
      Orchestrator → Researcher → Analyst → Writer → Reviewer
                          ↕           ↕
                     (collusion clique: Researcher ↔ Analyst ↔ Writer)

    Problems:
    - Orchestrator is a SPOF (only entry point, linear chain)
    - Researcher/Analyst/Writer form a dense collusion clique
    - No fallback paths — every agent has exactly 1 upstream
    - No timeout handling
    """
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
        # Collusion clique: dense cross-talk between Researcher, Analyst, Writer
        InteractionEvent(
            source_agent_id=analyst.id,
            target_agent_id=researcher.id,
            event_type=EventType.CONTEXT_SHARE,
            payload={"data": "sharing back"},
        ),
        InteractionEvent(
            source_agent_id=writer.id,
            target_agent_id=researcher.id,
            event_type=EventType.CONTEXT_SHARE,
            payload={"data": "writer feedback"},
        ),
        InteractionEvent(
            source_agent_id=writer.id,
            target_agent_id=analyst.id,
            event_type=EventType.CONTEXT_SHARE,
            payload={"data": "draft notes"},
        ),
    ]

    return SwarmProbe(
        swarm_name="research-crew",
        framework="static",
        agents=agents,
        events=events,
    )


# ---------------------------------------------------------------------------
# AFTER: improved architecture
# ---------------------------------------------------------------------------


def build_after() -> SwarmProbe:
    """
    Same 5 agents + 1 backup orchestrator, with fixes:
      Orchestrator ──→ Researcher → Analyst → Writer → Reviewer
      BackupOrch   ──→ Researcher                ↑
                       Analyst ───────────────────┘  (fallback path)

    Fixes applied:
    - BackupOrch added: Orchestrator is no longer a SPOF
    - Collusion clique broken: removed Writer↔Researcher cross-talk
    - Writer has fallback: Analyst can also feed Writer directly
    - Reviewer reports back to Orchestrator (closes loop)
    """
    orch = AgentNode(name="Orchestrator", role="manager")
    backup = AgentNode(name="BackupOrch", role="manager")
    researcher = AgentNode(name="Researcher", role="researcher")
    analyst = AgentNode(name="Analyst", role="analyst")
    writer = AgentNode(name="Writer", role="writer")
    reviewer = AgentNode(name="Reviewer", role="reviewer")

    agents = [orch, backup, researcher, analyst, writer, reviewer]

    events = [
        # Main pipeline (same as before)
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
        # FIX 1: BackupOrch also feeds Researcher (redundant entry)
        InteractionEvent(
            source_agent_id=backup.id,
            target_agent_id=researcher.id,
            event_type=EventType.TASK_DELEGATE,
            payload={"task": "backup research request"},
        ),
        # FIX 2: Analyst feedback only goes to Writer (no clique cross-talk)
        InteractionEvent(
            source_agent_id=analyst.id,
            target_agent_id=researcher.id,
            event_type=EventType.CONTEXT_SHARE,
            payload={"data": "analysis feedback"},
        ),
        # FIX 3: Reviewer closes loop back to Orchestrator
        InteractionEvent(
            source_agent_id=reviewer.id,
            target_agent_id=orch.id,
            event_type=EventType.AGENT_RESPONSE,
            payload={"result": "review complete"},
        ),
    ]

    return SwarmProbe(
        swarm_name="research-crew",
        framework="static",
        agents=agents,
        events=events,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60)
    print("  BEFORE: Running SwarmProbe on problematic architecture")
    print("=" * 60)
    before_probe = build_before()
    before_report = before_probe.run_all()
    before_report.print_summary()
    before_json = before_report.to_json("before.json", graph=before_probe.graph)
    print("Saved: before.json\n")

    print("=" * 60)
    print("  AFTER: Running SwarmProbe on improved architecture")
    print("=" * 60)
    after_probe = build_after()
    after_report = after_probe.run_all()
    after_report.print_summary()
    after_json = after_report.to_json("after.json", graph=after_probe.graph)
    print("Saved: after.json\n")

    print("=" * 60)
    print("  COMPARISON: What changed?")
    print("=" * 60)
    comparator = ReportComparator()
    result = comparator.compare(before_json, after_json)
    comparator.print_comparison(result)


if __name__ == "__main__":
    main()
