"""Tests for the CostRiskAttack (static topology-only token-waste risk scoring)."""

from __future__ import annotations

import json

import pytest

from swarm_test import (
    AgentNode,
    EventType,
    InteractionEvent,
    Severity,
    SwarmProbe,
)
from swarm_test.attacks.cost_risk import CostRiskAttack
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


def _titles(result) -> list[str]:
    return [f.title for f in result.findings]


def _findings_by_factor(result, factor: str):
    return [f for f in result.findings if f.evidence.get("factor") == factor]


# ---------------------------------------------------------------------------
# 1. Unbounded loop → CRITICAL cost risk
# ---------------------------------------------------------------------------


def test_unbounded_loop_is_critical_cost_risk() -> None:
    graph, _ = _build_graph(
        agents=["A", "B"],
        edges=[("A", "B"), ("B", "A")],
    )
    result = CostRiskAttack().run(graph)

    unbounded = _findings_by_factor(result, "unbounded_loop")
    assert unbounded, f"Expected unbounded-loop finding, got titles: {_titles(result)}"
    assert all(f.severity == Severity.CRITICAL for f in unbounded)
    assert all("CRITICAL" in f.title for f in unbounded)
    # Score should be HIGH or SEVERE — definitely well above LOW.
    score = result.metrics["cost_risk_score"]
    assert score >= 20, f"Expected meaningful score from unbounded loop, got {score}"
    assert result.metrics["cost_risk_verdict"] in {"MODERATE", "HIGH", "SEVERE"}


# ---------------------------------------------------------------------------
# 2. Feedback loop score scales with cycle length
# ---------------------------------------------------------------------------


def test_feedback_loop_scales_cost_with_length() -> None:
    # Compare two feedback cycles whose raw weights both exceed the HIGH
    # severity floor (50), so the additive length-based scaling is observable
    # in the final score. A short cycle below the floor would clamp to the
    # floor and hide the scaling — which is correct, but not what this test
    # is exercising.
    def _cycle_with_exit(n: int):
        nodes = [f"N{i}" for i in range(n)] + ["Exit"]
        cycle_edges = [(f"N{i}", f"N{(i + 1) % n}") for i in range(n)]
        return _build_graph(agents=nodes, edges=cycle_edges + [("N0", "Exit")])

    short_graph, _ = _cycle_with_exit(8)
    long_graph, _ = _cycle_with_exit(14)

    short_score = CostRiskAttack().run(short_graph).metrics["cost_risk_score"]
    long_score = CostRiskAttack().run(long_graph).metrics["cost_risk_score"]

    assert long_score > short_score, (
        f"Longer feedback loop should add more cost-risk weight "
        f"(short={short_score}, long={long_score})"
    )


# ---------------------------------------------------------------------------
# 3. Fragile single-upstream dependency → retry-prone cost
# ---------------------------------------------------------------------------


def test_fragile_dependency_adds_retry_cost() -> None:
    # B has exactly one upstream (A) and a downstream (C) — fragile retry-prone link.
    graph, _ = _build_graph(
        agents=["A", "B", "C"],
        edges=[("A", "B"), ("B", "C")],
    )
    result = CostRiskAttack().run(graph)

    retry = _findings_by_factor(result, "retry_prone")
    assert retry, f"Expected retry-prone finding, got titles: {_titles(result)}"
    # Label in the title is MEDIUM-HIGH; severity is HIGH on the Finding object.
    assert any("MEDIUM-HIGH" in f.title for f in retry)
    assert all(f.severity == Severity.HIGH for f in retry)


# ---------------------------------------------------------------------------
# 4. Long critical path → MEDIUM cost
# ---------------------------------------------------------------------------


def test_long_critical_path_adds_cost() -> None:
    # 8-node linear chain → 7 hops, above the 5-hop threshold.
    chain = ["N0", "N1", "N2", "N3", "N4", "N5", "N6", "N7"]
    edges = list(zip(chain, chain[1:]))
    graph, _ = _build_graph(agents=chain, edges=edges)
    result = CostRiskAttack().run(graph)

    long_path = _findings_by_factor(result, "long_critical_path")
    assert long_path, f"Expected long-critical-path finding, got titles: {_titles(result)}"
    assert long_path[0].severity == Severity.MEDIUM
    assert long_path[0].evidence["hops"] >= 7


# ---------------------------------------------------------------------------
# 5. High fan-out node → MEDIUM cost
# ---------------------------------------------------------------------------


def test_high_fanout_adds_cost() -> None:
    # Hub calls 6 downstream agents — well above the 4-out-degree threshold.
    agents = ["Hub", "W1", "W2", "W3", "W4", "W5", "W6"]
    edges = [("Hub", w) for w in agents[1:]]
    graph, _ = _build_graph(agents=agents, edges=edges)
    result = CostRiskAttack().run(graph)

    fanout = _findings_by_factor(result, "high_fanout")
    assert fanout, f"Expected high-fanout finding, got titles: {_titles(result)}"
    assert fanout[0].severity == Severity.MEDIUM
    assert fanout[0].evidence["out_degree"] == 6
    assert "Hub" in fanout[0].title


# ---------------------------------------------------------------------------
# 6. Clean DAG has low cost risk
# ---------------------------------------------------------------------------


def test_clean_dag_low_cost_risk() -> None:
    # Fan-in DAG: two roots feed a worker that produces a single sink.
    # No cycles, no single-upstream-with-downstream node (Worker has two
    # upstreams; Sink has no downstream), short path, low fan-out. This
    # topology is genuinely clean — no findings, no severity floor.
    graph, _ = _build_graph(
        agents=["Root1", "Root2", "Worker", "Sink"],
        edges=[("Root1", "Worker"), ("Root2", "Worker"), ("Worker", "Sink")],
    )
    result = CostRiskAttack().run(graph)

    score = result.metrics["cost_risk_score"]
    verdict = result.metrics["cost_risk_verdict"]
    assert score <= 24, f"Clean DAG should have LOW cost risk, got {score}"
    assert verdict == "LOW", f"Expected LOW verdict, got {verdict}"


# ---------------------------------------------------------------------------
# 7. Cost Risk Score stays in 0-100
# ---------------------------------------------------------------------------


def test_cost_risk_score_in_range_0_100() -> None:
    # Worst-case-ish: self-loop, unbounded cycle, fan-out, retry chain.
    agents = ["A", "B", "C", "D", "E", "F", "G", "H"]
    edges = [
        ("A", "A"),  # self-loop
        ("B", "C"),
        ("C", "B"),  # unbounded 2-cycle
        ("D", "E"),
        ("D", "F"),
        ("D", "G"),
        ("D", "H"),
        ("D", "A"),  # fan-out
        ("E", "F"),
        ("F", "G"),
        ("G", "H"),  # long-ish chain
    ]
    graph, _ = _build_graph(agents=agents, edges=edges)
    result = CostRiskAttack().run(graph)

    score = result.metrics["cost_risk_score"]
    assert 0 <= score <= 100, f"Score out of range: {score}"

    # Also: an empty graph reports score 0.
    empty_result = CostRiskAttack().run(SwarmGraph())
    assert empty_result.metrics["cost_risk_score"] == 0
    assert empty_result.metrics["cost_risk_verdict"] == "LOW"


# ---------------------------------------------------------------------------
# 8. No dollar amounts / currency in any output
# ---------------------------------------------------------------------------


def test_no_dollar_amounts_in_output() -> None:
    """Free/paid boundary: cost_risk findings must never quote a dollar figure."""
    graph, _ = _build_graph(
        agents=["A", "B", "C", "D", "E", "F", "G", "H"],
        edges=[
            ("A", "A"),
            ("B", "C"),
            ("C", "B"),
            ("D", "E"),
            ("D", "F"),
            ("D", "G"),
            ("D", "H"),
            ("E", "F"),
            ("F", "G"),
            ("G", "H"),
        ],
    )
    result = CostRiskAttack().run(graph)

    forbidden = ("$", "USD", "EUR", "GBP", "¥", "€", "£", "cents", "dollars")
    for finding in result.findings:
        blob = " ".join(
            [
                finding.title,
                finding.description,
                finding.remediation,
                json.dumps(finding.evidence, default=str),
            ]
        )
        for token in forbidden:
            assert token not in blob, (
                f"Forbidden currency token '{token}' appeared in cost_risk output. "
                f"Finding title: {finding.title}"
            )


# ---------------------------------------------------------------------------
# 9. Finding identities are stable across runs (deterministic IDs)
# ---------------------------------------------------------------------------


def test_finding_ids_stable_across_runs() -> None:
    edges = [
        ("A", "B"),
        ("B", "A"),
        ("A", "C"),
        ("C", "D"),
        ("D", "E"),
        ("E", "F"),
        ("F", "G"),
        ("G", "H"),
    ]
    agents = ["A", "B", "C", "D", "E", "F", "G", "H"]

    def run_once() -> set[str]:
        probe = SwarmProbe(swarm_name="cost-risk-id-test")
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
        cost_ids = {f["finding_id"] for f in json_blob["findings"] if f["risk_type"] == "cost_risk"}
        assert cost_ids, "cost_risk produced no findings on the test topology"
        return cost_ids

    ids_a = run_once()
    ids_b = run_once()
    assert (
        ids_a == ids_b
    ), f"cost_risk finding_ids drifted across runs.\nA: {sorted(ids_a)}\nB: {sorted(ids_b)}"


# ---------------------------------------------------------------------------
# 10. cost_risk is registered by default and is disableable
# ---------------------------------------------------------------------------


def test_cost_risk_registered_and_disableable() -> None:
    # Default probe registers cost_risk.
    probe = SwarmProbe(swarm_name="cost-risk-default")
    probe.graph.add_agent(AgentNode(name="A", role="worker"))
    probe.graph.add_agent(AgentNode(name="B", role="worker"))
    report = probe.run_all()
    test_names = {r.test_name for r in report.test_results}
    assert "cost_risk" in test_names, f"cost_risk missing from default run; got {test_names}"

    # Disabling via config drops it from the run.
    config = SwarmConfig(disabled_tests=["cost_risk"])
    probe2 = SwarmProbe(swarm_name="cost-risk-disabled", config=config)
    probe2.graph.add_agent(AgentNode(name="A", role="worker"))
    probe2.graph.add_agent(AgentNode(name="B", role="worker"))
    report2 = probe2.run_all()
    test_names2 = {r.test_name for r in report2.test_results}
    assert (
        "cost_risk" not in test_names2
    ), f"cost_risk ran despite being disabled; got {test_names2}"


# ---------------------------------------------------------------------------
# Bonus: every finding description carries the estimate-vs-runtime note
# ---------------------------------------------------------------------------


def test_estimate_note_present_in_every_finding() -> None:
    graph, _ = _build_graph(
        agents=["A", "B"],
        edges=[("A", "B"), ("B", "A")],
    )
    result = CostRiskAttack().run(graph)
    assert result.findings, "Expected at least one finding"
    for f in result.findings:
        assert (
            "structural estimate" in f.description.lower()
        ), f"Missing estimate disclaimer in: {f.title}"
        assert "execution data" in f.description.lower(), f"Missing runtime-data note in: {f.title}"


# ---------------------------------------------------------------------------
# Bonus: empty graph is a no-op
# ---------------------------------------------------------------------------


def test_empty_graph_no_findings() -> None:
    result = CostRiskAttack().run(SwarmGraph())
    assert result.findings == []
    assert result.metrics["cost_risk_score"] == 0


# ---------------------------------------------------------------------------
# Severity-floor tests — score must never under-report the worst finding.
# ---------------------------------------------------------------------------


def test_unbounded_loop_floors_score_to_severe() -> None:
    """A CRITICAL cost_risk finding floors the score into the SEVERE band."""
    graph, _ = _build_graph(
        agents=["A", "B"],
        edges=[("A", "B"), ("B", "A")],
    )
    result = CostRiskAttack().run(graph)

    # Sanity: the unbounded-loop CRITICAL finding is actually produced.
    assert any(f.severity == Severity.CRITICAL for f in result.findings)
    score = result.metrics["cost_risk_score"]
    verdict = result.metrics["cost_risk_verdict"]
    assert score >= 75, f"CRITICAL finding must floor score to SEVERE band, got {score}"
    assert verdict == "SEVERE", f"Expected SEVERE band, got {verdict}"


def test_high_finding_floors_to_high() -> None:
    """A HIGH cost_risk finding (no CRITICAL) floors the score into the HIGH band."""
    # Linear chain A→B→C: B has exactly one upstream and one downstream →
    # one retry-prone HIGH finding. No cycles, no self-loop, so no CRITICAL.
    graph, _ = _build_graph(
        agents=["A", "B", "C"],
        edges=[("A", "B"), ("B", "C")],
    )
    result = CostRiskAttack().run(graph)

    severities = {f.severity for f in result.findings}
    assert Severity.HIGH in severities, "Expected at least one HIGH finding"
    assert Severity.CRITICAL not in severities, "Did not expect any CRITICAL finding"

    score = result.metrics["cost_risk_score"]
    verdict = result.metrics["cost_risk_verdict"]
    assert score >= 50, f"HIGH finding must floor score to >= 50, got {score}"
    assert verdict in {"HIGH", "SEVERE"}, f"Expected HIGH/SEVERE band, got {verdict}"


def test_clean_dag_stays_low() -> None:
    """A truly clean DAG (no cost_risk findings) stays in the LOW band."""
    # Fan-in DAG with no single-upstream-with-downstream nodes → 0 findings.
    graph, _ = _build_graph(
        agents=["Root1", "Root2", "Worker", "Sink"],
        edges=[("Root1", "Worker"), ("Root2", "Worker"), ("Worker", "Sink")],
    )
    result = CostRiskAttack().run(graph)

    assert (
        result.findings == []
    ), f"Expected zero findings on a clean DAG, got: {[f.title for f in result.findings]}"
    score = result.metrics["cost_risk_score"]
    verdict = result.metrics["cost_risk_verdict"]
    assert score <= 24, f"Clean DAG score must be in LOW band (0-24), got {score}"
    assert verdict == "LOW", f"Expected LOW band, got {verdict}"


def test_verdict_band_thresholds_align_with_floors() -> None:
    """The band label derives from the same thresholds as the severity floors.

    Specifically: 0-24 LOW, 25-49 MODERATE, 50-74 HIGH, 75-100 SEVERE — and
    the CRITICAL/HIGH/MEDIUM floor values 75/50/25 land exactly on the band
    starts, so the score number and the verdict word always agree.
    """
    from swarm_test.attacks.cost_risk import _verdict_for

    assert _verdict_for(0) == "LOW"
    assert _verdict_for(24) == "LOW"
    assert _verdict_for(25) == "MODERATE"
    assert _verdict_for(49) == "MODERATE"
    assert _verdict_for(50) == "HIGH"
    assert _verdict_for(74) == "HIGH"
    assert _verdict_for(75) == "SEVERE"
    assert _verdict_for(100) == "SEVERE"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
