"""Tests for per-agent redundancy scoring."""

from __future__ import annotations

from swarm_test import AgentNode, EventType, InteractionEvent, SwarmProbe
from swarm_test.core.graph import SwarmGraph
from swarm_test.core.models import redundancy_level

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(name: str, role: str = "worker", tools: list[str] | None = None) -> AgentNode:
    return AgentNode(
        name=name,
        role=role,
        framework="test",
        metadata={"tools": tools or []},
    )


def _delegate(src: AgentNode, dst: AgentNode) -> InteractionEvent:
    return InteractionEvent(
        source_agent_id=src.id,
        target_agent_id=dst.id,
        event_type=EventType.TASK_DELEGATE,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_agent_zero_redundancy():
    """A graph with a single agent has zero redundancy — nothing else to share work."""
    graph = SwarmGraph()
    graph.add_agent(_make_agent("Solo", role="orchestrator"))
    score = graph.calculate_redundancy_score(list(graph.agents.keys())[0])
    assert score == 0.0


def test_linear_chain_middle_lowest():
    """A->B->C: B is the middle hop and should score lowest (SPOF)."""
    a, b, c = (
        _make_agent("A", "researcher"),
        _make_agent("B", "analyst"),
        _make_agent("C", "writer"),
    )
    graph = SwarmGraph()
    for ag in (a, b, c):
        graph.add_agent(ag)
    graph.record_event(_delegate(a, b))
    graph.record_event(_delegate(b, c))

    scores = graph.calculate_all_redundancy_scores()
    assert scores[b.id] < scores[a.id]
    assert scores[b.id] < scores[c.id]
    # B is an articulation point — capped low
    assert scores[b.id] < 20.0


def test_parallel_agents_high_redundancy():
    """A->[B1,B2]->C: B1 and B2 share a role and split work — both high redundancy."""
    a = _make_agent("A", "researcher")
    b1 = _make_agent("B1", "analyst")
    b2 = _make_agent("B2", "analyst")
    c = _make_agent("C", "writer")
    graph = SwarmGraph()
    for ag in (a, b1, b2, c):
        graph.add_agent(ag)
    graph.record_event(_delegate(a, b1))
    graph.record_event(_delegate(a, b2))
    graph.record_event(_delegate(b1, c))
    graph.record_event(_delegate(b2, c))

    scores = graph.calculate_all_redundancy_scores()
    # Parallel siblings should each be more redundant than a SPOF in a linear chain
    assert scores[b1.id] > 30.0
    assert scores[b2.id] > 30.0
    # And similar to one another
    assert abs(scores[b1.id] - scores[b2.id]) < 10.0


def test_hub_spoke_hub_is_spof():
    """Hub connecting 5 spokes — removing the hub disconnects everything."""
    hub = _make_agent("Hub", role="orchestrator")
    spokes = [_make_agent(f"S{i}", role=f"worker_{i}") for i in range(5)]
    graph = SwarmGraph()
    graph.add_agent(hub)
    for s in spokes:
        graph.add_agent(s)
        graph.record_event(_delegate(hub, s))

    scores = graph.calculate_all_redundancy_scores()
    # Hub is a critical SPOF — near zero
    assert scores[hub.id] < 20.0
    # Spokes are leaves; removing one still leaves the rest connected to hub
    for s in spokes:
        assert scores[s.id] > scores[hub.id]


def test_fully_connected_high_scores():
    """All-to-all graph — every agent is redundant."""
    agents = [_make_agent(f"N{i}", role="peer") for i in range(4)]
    graph = SwarmGraph()
    for a in agents:
        graph.add_agent(a)
    for src in agents:
        for dst in agents:
            if src.id != dst.id:
                graph.record_event(_delegate(src, dst))

    scores = graph.calculate_all_redundancy_scores()
    for s in scores.values():
        assert s > 60.0


def test_unique_tool_lowers_score():
    """An agent whose tools no peer can run has lower redundancy than a peer
    whose tools are fully covered."""
    a = _make_agent("Unique", role="peer", tools=["secret_tool"])
    b = _make_agent("Covered", role="peer", tools=["shared_tool"])
    c = _make_agent("CoveredPeer", role="peer", tools=["shared_tool"])
    graph = SwarmGraph()
    for ag in (a, b, c):
        graph.add_agent(ag)
    graph.record_event(_delegate(a, b))
    graph.record_event(_delegate(b, c))
    graph.record_event(_delegate(c, a))

    scores = graph.calculate_all_redundancy_scores()
    # 'Covered' has its tool replicated on 'CoveredPeer'; 'Unique' does not.
    assert scores[b.id] > scores[a.id]


def test_redundancy_in_json_export():
    """JSON report carries redundancy_scores and overall_redundancy."""
    agents = [_make_agent(f"N{i}", role="peer") for i in range(3)]
    events = [_delegate(agents[i], agents[i + 1]) for i in range(2)]
    probe = SwarmProbe(swarm_name="r-json", agents=agents, events=events)
    report = probe.run_all()
    data = report.to_json()

    assert "redundancy_scores" in data
    assert "overall_redundancy" in data
    assert len(data["redundancy_scores"]) == 3
    for row in data["redundancy_scores"]:
        assert {"agent_id", "agent_name", "score", "level"}.issubset(row)
        assert 0.0 <= row["score"] <= 100.0
        assert row["level"] in {
            "IRREPLACEABLE",
            "LOW",
            "MODERATE",
            "HIGH",
            "FULLY REDUNDANT",
        }
    # Overall is the mean
    expected = round(sum(r["score"] for r in data["redundancy_scores"]) / 3, 2)
    assert abs(data["overall_redundancy"] - expected) < 0.01


def test_redundancy_levels_correct():
    """The redundancy_level helper maps boundary values correctly."""
    assert redundancy_level(0.0) == "IRREPLACEABLE"
    assert redundancy_level(20.0) == "IRREPLACEABLE"
    assert redundancy_level(20.01) == "LOW"
    assert redundancy_level(40.0) == "LOW"
    assert redundancy_level(50.0) == "MODERATE"
    assert redundancy_level(60.0) == "MODERATE"
    assert redundancy_level(70.0) == "HIGH"
    assert redundancy_level(80.0) == "HIGH"
    assert redundancy_level(95.0) == "FULLY REDUNDANT"
    assert redundancy_level(100.0) == "FULLY REDUNDANT"
