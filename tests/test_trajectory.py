"""Tests for the TrajectoryAttack (loop / cycle / runaway-path detection)."""

from __future__ import annotations

import pytest

from swarm_test import (
    AgentNode,
    EventType,
    InteractionEvent,
    Severity,
    SwarmProbe,
)
from swarm_test.attacks.trajectory import TrajectoryAttack
from swarm_test.config import SwarmConfig
from swarm_test.core.graph import SwarmGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_graph(
    agents: list[str], edges: list[tuple[str, str]]
) -> tuple[SwarmGraph, dict[str, str]]:
    g = SwarmGraph()
    node_ids: dict[str, str] = {}
    for name in agents:
        node = AgentNode(name=name, role="worker")
        g.add_agent(node)
        node_ids[name] = node.id
    for src, dst in edges:
        g.record_event(
            InteractionEvent(
                source_agent_id=node_ids[src],
                target_agent_id=node_ids[dst],
                event_type=EventType.TASK_DELEGATE,
                payload={},
                success=True,
            )
        )
    return g, node_ids


def _findings_by_severity(result, severity: Severity):
    return [f for f in result.findings if f.severity == severity]


def _titles(result) -> list[str]:
    return [f.title for f in result.findings]


# ---------------------------------------------------------------------------
# 1. 2-node mutual cycle with exit → MEDIUM ping-pong
# ---------------------------------------------------------------------------


def test_simple_two_node_cycle_with_exit_is_medium() -> None:
    graph, _ = _build_graph(
        agents=["A", "B", "C"],
        edges=[("A", "B"), ("B", "A"), ("A", "C")],
    )
    result = TrajectoryAttack().run(graph)

    medium = _findings_by_severity(result, Severity.MEDIUM)
    assert any(
        "Ping-pong" in f.title for f in medium
    ), f"Expected MEDIUM ping-pong finding, got titles: {_titles(result)}"
    # Should NOT be classified as unbounded (exit edge exists).
    assert not any("Unbounded" in f.title for f in result.findings)


# ---------------------------------------------------------------------------
# 2. Cycle with no exit → CRITICAL unbounded
# ---------------------------------------------------------------------------


def test_unbounded_cycle_no_exit_is_critical() -> None:
    graph, _ = _build_graph(
        agents=["A", "B"],
        edges=[("A", "B"), ("B", "A")],
    )
    result = TrajectoryAttack().run(graph)

    crits = _findings_by_severity(result, Severity.CRITICAL)
    assert crits, f"Expected CRITICAL unbounded loop, got {_titles(result)}"
    assert any("Unbounded" in f.title for f in crits)


# ---------------------------------------------------------------------------
# 3. 3-node feedback loop with exit → HIGH
# ---------------------------------------------------------------------------


def test_three_node_feedback_loop_is_high() -> None:
    graph, _ = _build_graph(
        agents=["A", "B", "C", "D"],
        edges=[("A", "B"), ("B", "C"), ("C", "A"), ("A", "D")],
    )
    result = TrajectoryAttack().run(graph)

    highs = _findings_by_severity(result, Severity.HIGH)
    assert any(
        "feedback loop" in f.title for f in highs
    ), f"Expected HIGH feedback loop finding, got {_titles(result)}"


# ---------------------------------------------------------------------------
# 4. Self-loop → HIGH self-invocation
# ---------------------------------------------------------------------------


def test_self_loop_is_high() -> None:
    graph, _ = _build_graph(
        agents=["A"],
        edges=[("A", "A")],
    )
    result = TrajectoryAttack().run(graph)

    highs = _findings_by_severity(result, Severity.HIGH)
    assert len(highs) == 1
    assert "Self-invocation" in highs[0].title
    assert highs[0].evidence.get("loop_type") == "self"


# ---------------------------------------------------------------------------
# 5. Duplicate parallel edges → MEDIUM repeated calls
# ---------------------------------------------------------------------------


def test_duplicate_edges_flagged() -> None:
    graph, _ = _build_graph(
        agents=["A", "B"],
        edges=[("A", "B"), ("A", "B"), ("A", "B")],
    )
    result = TrajectoryAttack().run(graph)

    mediums = _findings_by_severity(result, Severity.MEDIUM)
    dup = [f for f in mediums if "Repeated calls" in f.title]
    assert len(dup) == 1, f"Expected one MEDIUM repeated-calls finding, got {_titles(result)}"
    assert dup[0].evidence["call_count"] == 3
    assert dup[0].evidence["source"] == "A"
    assert dup[0].evidence["target"] == "B"


# ---------------------------------------------------------------------------
# 6. Cycle longer than max_trajectory_depth → MEDIUM deep-path finding
# ---------------------------------------------------------------------------


def test_deep_cyclic_path_flagged() -> None:
    # 7-node cycle with an exit (so we get HIGH feedback + MEDIUM deep).
    cycle_nodes = ["A", "B", "C", "D", "E", "F", "G"]
    cycle_edges = list(zip(cycle_nodes, cycle_nodes[1:] + [cycle_nodes[0]]))
    cycle_edges.append(("A", "Exit"))  # exit edge

    graph, _ = _build_graph(
        agents=cycle_nodes + ["Exit"],
        edges=cycle_edges,
    )
    result = TrajectoryAttack(max_trajectory_depth=5).run(graph)

    deep = [f for f in result.findings if "Deep cyclic path" in f.title]
    assert len(deep) == 1, f"Expected one deep cyclic path finding, got {_titles(result)}"
    assert deep[0].severity == Severity.MEDIUM
    assert deep[0].evidence["cycle_length"] == 7

    # Also expect the corresponding HIGH feedback loop finding.
    highs = _findings_by_severity(result, Severity.HIGH)
    assert any("feedback loop" in f.title for f in highs)


# ---------------------------------------------------------------------------
# 7. Pure DAG → no findings
# ---------------------------------------------------------------------------


def test_acyclic_graph_no_findings() -> None:
    graph, _ = _build_graph(
        agents=["A", "B", "C", "D"],
        edges=[("A", "B"), ("B", "C"), ("C", "D"), ("A", "D")],
    )
    result = TrajectoryAttack().run(graph)

    assert result.findings == [], f"DAG should produce no findings, got {_titles(result)}"


# ---------------------------------------------------------------------------
# 8. Finding identities are stable across rebuilds of the same topology
# ---------------------------------------------------------------------------


def test_finding_ids_stable_across_runs() -> None:
    """Two independent runs against the same topology produce identical stable IDs.

    Agent UUIDs are regenerated each run, but the stable JSON ``finding_id``
    hashes on ``test_name + normalized_title + primary_agent_name`` — all of
    which are derived from the topology, so the hash must be invariant.
    """
    edges = [("A", "B"), ("B", "C"), ("C", "A"), ("A", "Exit"), ("X", "Y"), ("X", "Y")]
    agents = ["A", "B", "C", "Exit", "X", "Y"]

    def run_once() -> set[str]:
        probe = SwarmProbe(swarm_name="trajectory-id-test")
        for name in agents:
            probe.graph.add_agent(AgentNode(name=name, role="worker"))
        name_to_id = {data["name"]: nid for nid, data in probe.graph.graph.nodes(data=True)}
        for src, dst in edges:
            probe.graph.record_event(
                InteractionEvent(
                    source_agent_id=name_to_id[src],
                    target_agent_id=name_to_id[dst],
                    event_type=EventType.TASK_DELEGATE,
                )
            )
        report = probe.run_all()
        json_blob = report.to_json(graph=probe.graph)
        traj_ids = {
            f["finding_id"] for f in json_blob["findings"] if f["risk_type"] == "trajectory"
        }
        assert traj_ids, "Trajectory test produced no findings on the test topology"
        return traj_ids

    ids_run_a = run_once()
    ids_run_b = run_once()
    assert ids_run_a == ids_run_b, (
        f"Trajectory finding_ids drifted across runs.\nA: {sorted(ids_run_a)}\n"
        f"B: {sorted(ids_run_b)}"
    )


# ---------------------------------------------------------------------------
# 9. Probe registers trajectory_analysis by default
# ---------------------------------------------------------------------------


def test_trajectory_registered_in_probe() -> None:
    probe = SwarmProbe(swarm_name="trajectory-default-test")
    probe.graph.add_agent(AgentNode(name="A", role="worker"))
    probe.graph.add_agent(AgentNode(name="B", role="worker"))
    report = probe.run_all()

    test_names = {r.test_name for r in report.test_results}
    assert (
        "trajectory_analysis" in test_names
    ), f"trajectory_analysis missing from default run; got {test_names}"


# ---------------------------------------------------------------------------
# 10. disabled_tests excludes trajectory_analysis
# ---------------------------------------------------------------------------


def test_disabled_via_config() -> None:
    config = SwarmConfig(disabled_tests=["trajectory_analysis"])
    probe = SwarmProbe(swarm_name="trajectory-disabled-test", config=config)
    probe.graph.add_agent(AgentNode(name="A", role="worker"))
    probe.graph.add_agent(AgentNode(name="B", role="worker"))
    report = probe.run_all()

    test_names = {r.test_name for r in report.test_results}
    assert (
        "trajectory_analysis" not in test_names
    ), f"trajectory_analysis ran despite being disabled; got {test_names}"


# ---------------------------------------------------------------------------
# Bonus: config-driven depth override actually wires through
# ---------------------------------------------------------------------------


def test_max_trajectory_depth_from_config() -> None:
    config = SwarmConfig(max_trajectory_depth=3)
    cycle_nodes = ["A", "B", "C", "D"]  # 4-node cycle
    cycle_edges = list(zip(cycle_nodes, cycle_nodes[1:] + [cycle_nodes[0]]))
    cycle_edges.append(("A", "Exit"))

    graph, _ = _build_graph(agents=cycle_nodes + ["Exit"], edges=cycle_edges)
    probe = SwarmProbe(swarm_name="trajectory-depth-test", config=config)
    # Re-use our pre-built graph topology by attaching it to the probe.
    probe.graph = graph
    report = probe.run_all()

    traj = next(r for r in report.test_results if r.test_name == "trajectory_analysis")
    deep_findings = [f for f in traj.findings if "Deep cyclic path" in f.title]
    assert deep_findings, (
        f"Expected deep cyclic path at depth=3 with a 4-node cycle; " f"got titles {_titles(traj)}"
    )


# ---------------------------------------------------------------------------
# Bonus: empty graph is a no-op
# ---------------------------------------------------------------------------


def test_empty_graph_is_passing() -> None:
    graph = SwarmGraph()
    result = TrajectoryAttack().run(graph)
    assert result.findings == []
    assert result.metrics.get("note") == "empty graph"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
