"""Tests for the agent role taxonomy classifier."""

from __future__ import annotations

import networkx as nx

from swarm_test import AgentNode, EventType, InteractionEvent, SwarmProbe
from swarm_test.core.graph import SwarmGraph
from swarm_test.core.taxonomy import (
    AgentRole,
    classify_agent,
    classify_all,
    role_adjusted_severity,
)


def _make_agent(name: str, role: str = "worker") -> AgentNode:
    return AgentNode(name=name, role=role, framework="test")


def _delegate(src: AgentNode, dst: AgentNode) -> InteractionEvent:
    return InteractionEvent(
        source_agent_id=src.id,
        target_agent_id=dst.id,
        event_type=EventType.TASK_DELEGATE,
    )


def _build_graph(agents: list[AgentNode], edges: list[tuple[AgentNode, AgentNode]]) -> SwarmGraph:
    g = SwarmGraph()
    for a in agents:
        g.add_agent(a)
    for src, dst in edges:
        g.record_event(_delegate(src, dst))
    return g


def test_classify_orchestrator():
    """A hub with high out-degree should be classified ORCHESTRATOR."""
    hub = _make_agent("Hub", role="dispatcher")
    workers = [_make_agent(f"W{i}") for i in range(5)]
    g = _build_graph([hub, *workers], [(hub, w) for w in workers])

    role, conf = classify_agent(hub.id, g.graph, agents=g.agents)
    assert role == AgentRole.ORCHESTRATOR
    assert 0.0 <= conf <= 1.0
    assert conf > 0.4


def test_classify_aggregator():
    """A node receiving from many with low out-degree should be AGGREGATOR."""
    sources = [_make_agent(f"S{i}") for i in range(5)]
    sink = _make_agent("Sink")
    edges = [(s, sink) for s in sources]
    g = _build_graph([*sources, sink], edges)

    role, conf = classify_agent(sink.id, g.graph, agents=g.agents)
    assert role == AgentRole.AGGREGATOR
    assert conf > 0.4


def test_classify_worker():
    """A leaf node with single inbound edge should be WORKER."""
    hub = _make_agent("Hub", role="dispatcher")
    w1 = _make_agent("DoWorkA")
    w2 = _make_agent("DoWorkB")
    w3 = _make_agent("DoWorkC")
    g = _build_graph([hub, w1, w2, w3], [(hub, w1), (hub, w2), (hub, w3)])

    role, _ = classify_agent(w1.id, g.graph, agents=g.agents)
    assert role == AgentRole.WORKER


def test_classify_validator_by_name():
    """An agent named ValidatorAgent should be classified VALIDATOR."""
    a = _make_agent("Producer")
    v = _make_agent("ValidatorAgent", role="validator")
    out = _make_agent("Output")
    g = _build_graph([a, v, out], [(a, v), (v, out)])

    role, conf = classify_agent(v.id, g.graph, agents=g.agents)
    assert role == AgentRole.VALIDATOR
    assert conf > 0.3


def test_classify_gateway():
    """Pure source (in_deg=0) or pure sink (out_deg=0) should be GATEWAY."""
    entry = _make_agent("Entry", role="ingress")
    middle = _make_agent("Middle", role="processor")
    exit_ = _make_agent("Exit", role="egress")
    g = _build_graph([entry, middle, exit_], [(entry, middle), (middle, exit_)])

    role_entry, _ = classify_agent(entry.id, g.graph, agents=g.agents)
    role_exit, _ = classify_agent(exit_.id, g.graph, agents=g.agents)
    assert role_entry == AgentRole.GATEWAY
    assert role_exit == AgentRole.GATEWAY


def test_classify_monitor_by_name():
    """Agent named HealthMonitor should be classified MONITOR."""
    a = _make_agent("A")
    b = _make_agent("B")
    m = _make_agent("HealthMonitor", role="observer")
    g = _build_graph([a, b, m], [(a, b), (a, m), (b, m)])

    role, conf = classify_agent(m.id, g.graph, agents=g.agents)
    assert role == AgentRole.MONITOR
    assert conf > 0.2


def test_confidence_score():
    """All classifications return a confidence within [0.0, 1.0]."""
    hub = _make_agent("Hub")
    w = _make_agent("W")
    g = _build_graph([hub, w], [(hub, w)])

    for aid in [hub.id, w.id]:
        _, conf = classify_agent(aid, g.graph, agents=g.agents)
        assert 0.0 <= conf <= 1.0


def test_role_adjusted_severity_orchestrator():
    """Orchestrator with high blast radius gets downgraded — it's by design."""
    adjusted = role_adjusted_severity(AgentRole.ORCHESTRATOR, "blast_radius", "critical")
    assert adjusted == "high"

    adjusted2 = role_adjusted_severity(AgentRole.ORCHESTRATOR, "cascade_failure", "high")
    assert adjusted2 == "medium"


def test_role_adjusted_severity_worker():
    """Worker with high blast radius is a design smell — severity stays."""
    adjusted = role_adjusted_severity(AgentRole.WORKER, "blast_radius", "high")
    assert adjusted == "high"

    adjusted2 = role_adjusted_severity(AgentRole.WORKER, "blast_radius", "critical")
    assert adjusted2 == "critical"


def test_classify_all_returns_all_agents():
    """classify_all must return a classification for every agent."""
    agents = [_make_agent(f"A{i}") for i in range(4)]
    edges = [(agents[0], agents[1]), (agents[1], agents[2]), (agents[2], agents[3])]
    g = _build_graph(agents, edges)

    result = classify_all(g.graph, agents=g.agents)
    assert set(result.keys()) == {a.id for a in agents}
    for role, conf in result.values():
        assert role in {
            AgentRole.ORCHESTRATOR,
            AgentRole.WORKER,
            AgentRole.VALIDATOR,
            AgentRole.GATEWAY,
            AgentRole.AGGREGATOR,
            AgentRole.MONITOR,
            AgentRole.ROUTER,
            AgentRole.UNKNOWN,
        }
        assert 0.0 <= conf <= 1.0


def test_probe_populates_agent_roles():
    """SwarmProbe.run_all must populate report.agent_roles for every agent."""
    a = _make_agent("Hub", role="orchestrator")
    b = _make_agent("Worker1")
    c = _make_agent("Worker2")
    probe = SwarmProbe(
        swarm_name="role-tax",
        agents=[a, b, c],
        events=[_delegate(a, b), _delegate(a, c)],
    )
    report = probe.run_all()
    assert set(report.agent_roles.keys()) == {a.id, b.id, c.id}
    for info in report.agent_roles.values():
        assert "role" in info and "confidence" in info


def test_validator_context_leakage_upgraded():
    """A validator with context leakage should have its severity upgraded."""
    adjusted = role_adjusted_severity(AgentRole.VALIDATOR, "context_leakage", "medium")
    assert adjusted == "high"


def test_classify_handles_empty_graph():
    """An empty graph should yield no classifications."""
    g = nx.MultiDiGraph()
    assert classify_all(g) == {}
