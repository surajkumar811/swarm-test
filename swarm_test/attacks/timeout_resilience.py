"""Timeout Resilience Attack — evaluate how agents handle slow or unresponsive peers."""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from swarm_test.attacks.base import BaseAttack
from swarm_test.core.models import (
    EventType,
    Finding,
    Severity,
    TestResult,
    TestStatus,
)

logger = logging.getLogger(__name__)

# Simulated delay tiers in milliseconds
DELAY_TIERS_MS = [5_000, 15_000, 30_000]

# Events that represent inter-agent communication subject to timeouts
_COMM_EVENTS = {
    EventType.AGENT_CALL,
    EventType.AGENT_RESPONSE,
    EventType.TASK_DELEGATE,
    EventType.CONTEXT_SHARE,
    EventType.TOOL_USE,
}


class TimeoutResilienceAttack(BaseAttack):
    """
    Analyses the interaction graph for timeout-handling weaknesses.

    Checks performed:
    1. **Missing timeout evidence** — edges with no ``duration_ms`` set, meaning
       the interaction has no time-bounding.
    2. **Slow interactions** — edges whose ``duration_ms`` exceeds the simulated
       delay tiers (5 s, 15 s, 30 s).
    3. **No error/timeout events downstream** — if an agent has only successful
       downstream events with no recorded timeout or error events, it lacks
       graceful degradation evidence.
    4. **Single-path dependencies without timeout handling** — agents with
       exactly one upstream supplier and no recorded timeout events are fragile.
    """

    name = "timeout_resilience"
    description = (
        "Simulates slow-response scenarios (5 s, 15 s, 30 s) and checks "
        "whether downstream agents have timeout handling or fail gracefully."
    )

    def run(self, graph: Any) -> TestResult:
        findings: list[Finding] = []
        metrics: dict[str, Any] = {
            "total_edges_checked": 0,
            "edges_without_timeout": 0,
            "slow_interactions": 0,
            "agents_without_graceful_degradation": 0,
            "fragile_single_path_agents": 0,
        }

        nodes = list(graph.graph.nodes())
        edges = list(graph.graph.edges(data=True, keys=True))
        metrics["total_edges_checked"] = len(edges)

        if len(nodes) < 2:
            return TestResult(
                test_name=self.name,
                status=TestStatus.PASSED,
                findings=[],
                metrics={"note": "Need ≥2 agents for timeout analysis"},
            )

        role_ctx = getattr(graph, "role_context", None)
        # Fragile-spoke collapsing only applies to *declared* hubs. An
        # inferred-orchestrator's spokes are still flagged individually since
        # the user hasn't told us the dependency is intentional.
        hub_ids: set[str] = role_ctx.intentional_hubs if role_ctx is not None else set()

        # -- Check 1: edges with no duration_ms (no time-bounding) -----------
        untimed_edges = []
        for src, dst, _key, data in edges:
            event_type_str = data.get("event_type", "")
            try:
                evt = EventType(event_type_str)
            except ValueError:
                continue
            if evt not in _COMM_EVENTS:
                continue
            duration = data.get("duration_ms")
            if duration is None:
                untimed_edges.append((src, dst, data))

        metrics["edges_without_timeout"] = len(untimed_edges)

        if untimed_edges:
            severity = Severity.HIGH if len(untimed_edges) > 2 else Severity.MEDIUM
            affected = list({a for src, dst, _ in untimed_edges for a in (src, dst)})
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=severity,
                    title=f"{len(untimed_edges)} interaction(s) have no timeout configured",
                    description=(
                        f"{len(untimed_edges)} agent-to-agent interactions lack a "
                        f"recorded duration_ms, indicating no timeout is configured. "
                        f"A slow or unresponsive agent could block the entire pipeline."
                    ),
                    affected_agents=affected,
                    evidence={"untimed_edge_count": len(untimed_edges)},
                    remediation=(
                        f"Add timeout handling on the {len(untimed_edges)} "
                        f"untimed edge(s) — wrap inter-agent calls with an "
                        f"explicit timeout and circuit breaker."
                    ),
                )
            )

        # -- Check 2: slow interactions against delay tiers ------------------
        for src, dst, _key, data in edges:
            duration = data.get("duration_ms")
            if duration is None:
                continue
            for tier_ms in reversed(DELAY_TIERS_MS):
                if duration >= tier_ms:
                    src_name = graph.graph.nodes[src].get("name", src)
                    dst_name = graph.graph.nodes[dst].get("name", dst)
                    severity = (
                        Severity.CRITICAL
                        if tier_ms >= 30_000
                        else Severity.HIGH if tier_ms >= 15_000 else Severity.MEDIUM
                    )
                    metrics["slow_interactions"] += 1
                    findings.append(
                        Finding(
                            test_name=self.name,
                            severity=severity,
                            title=(
                                f"Slow interaction: {src_name} → {dst_name} "
                                f"({duration:.0f} ms ≥ {tier_ms} ms tier)"
                            ),
                            description=(
                                f"The interaction from '{src_name}' to '{dst_name}' "
                                f"took {duration:.0f} ms, exceeding the {tier_ms} ms "
                                f"delay tier. Downstream agents may stall or time out."
                            ),
                            affected_agents=[src, dst],
                            evidence={
                                "duration_ms": duration,
                                "tier_ms": tier_ms,
                            },
                            remediation=(
                                f"Add timeout handling to '{src_name}' — current "
                                f"response time {duration / 1000:.1f}s exceeds "
                                f"the {tier_ms / 1000:.0f}s threshold; add retry "
                                f"with backoff or a fallback response for '{dst_name}'."
                            ),
                        )
                    )
                    break  # Report highest matching tier only

        # -- Check 3: agents with no graceful degradation evidence -----------
        timeout_or_error_targets: set[str] = set()
        for event in graph.events:
            if event.event_type in (EventType.TIMEOUT, EventType.ERROR):
                timeout_or_error_targets.add(event.target_agent_id)
                timeout_or_error_targets.add(event.source_agent_id)

        no_timeout_agent_names: list[str] = []
        no_timeout_agent_ids: list[str] = []
        for node_id in nodes:
            in_degree = graph.graph.in_degree(node_id)
            if in_degree == 0:
                continue  # Root agent — no upstream to time out on
            if node_id not in timeout_or_error_targets:
                out_degree = graph.graph.out_degree(node_id)
                if out_degree > 0:
                    metrics["agents_without_graceful_degradation"] += 1
                    no_timeout_agent_ids.append(node_id)
                    no_timeout_agent_names.append(graph.graph.nodes[node_id].get("name", node_id))

        # Collapse: when more than 3 agents share the "no timeout handling"
        # condition, the gap is systemic (the framework lacks timeout
        # instrumentation) rather than a per-agent risk. Emit one MEDIUM
        # finding listing them all. For small graphs (≤3) keep per-agent
        # findings so the message stays specific.
        if no_timeout_agent_ids:
            if len(no_timeout_agent_ids) > 3:
                sample = ", ".join(sorted(no_timeout_agent_names)[:5])
                if len(no_timeout_agent_names) > 5:
                    sample += f", … (+{len(no_timeout_agent_names) - 5} more)"
                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=Severity.MEDIUM,
                        title=(f"No timeout handling across {len(no_timeout_agent_ids)} agents"),
                        description=(
                            f"{len(no_timeout_agent_ids)} agents ({sample}) have no "
                            f"TIMEOUT/ERROR events recorded. When this many agents lack "
                            f"timeout instrumentation, the gap is systemic rather than a "
                            f"per-agent design flaw — the framework or its adapter is not "
                            f"emitting timeout events."
                        ),
                        affected_agents=no_timeout_agent_ids,
                        evidence={
                            "agents_without_handling": sorted(no_timeout_agent_names),
                            "count": len(no_timeout_agent_ids),
                        },
                        remediation=(
                            f"Add timeout instrumentation at the framework / adapter "
                            f"layer so TIMEOUT or ERROR events are emitted when upstream "
                            f"calls exceed the configured threshold. Fixing this once "
                            f"covers all {len(no_timeout_agent_ids)} agents."
                        ),
                    )
                )
            else:
                for node_id, agent_name in zip(no_timeout_agent_ids, no_timeout_agent_names):
                    findings.append(
                        Finding(
                            test_name=self.name,
                            severity=Severity.MEDIUM,
                            title=f"No timeout handling evidence: {agent_name}",
                            description=(
                                f"Agent '{agent_name}' has upstream and downstream "
                                f"connections but no recorded timeout or error events, "
                                f"suggesting it may not handle slow responses gracefully."
                            ),
                            affected_agents=[node_id],
                            evidence={
                                "in_degree": graph.graph.in_degree(node_id),
                                "out_degree": graph.graph.out_degree(node_id),
                            },
                            remediation=(
                                f"Add timeout handling to '{agent_name}' — emit "
                                f"TIMEOUT or ERROR events when upstream calls exceed "
                                f"the configured threshold so failures propagate "
                                f"instead of stalling."
                            ),
                        )
                    )

        # -- Check 4: single-path dependencies without timeout handling ------
        # When the single upstream is an intentional hub, every spoke would
        # fire this finding — that's the whole point of the topology. Collapse
        # those into a single INFO finding; keep per-agent findings for
        # non-hub fragile dependencies.
        # Cycle members also share the "exactly one upstream" condition (their
        # cycle predecessor); collapse those into one cycle-cluster finding so
        # an N-node cycle doesn't emit N near-identical "fragile" rows.
        try:
            sccs = [scc for scc in nx.strongly_connected_components(graph.graph) if len(scc) >= 2]
        except Exception:
            sccs = []
        scc_membership: dict[str, frozenset[str]] = {}
        for scc in sccs:
            frozen = frozenset(scc)
            for nid in scc:
                scc_membership[nid] = frozen
        cycle_fragile: dict[frozenset[str], list[tuple[str, str, str, str]]] = {}

        hub_fragile_spokes: list[tuple[str, str]] = []
        for node_id in nodes:
            predecessors = list(graph.graph.predecessors(node_id))
            if len(predecessors) == 1 and node_id not in timeout_or_error_targets:
                metrics["fragile_single_path_agents"] += 1
                agent_name = graph.graph.nodes[node_id].get("name", node_id)
                upstream_id = predecessors[0]
                upstream_name = graph.graph.nodes[upstream_id].get("name", upstream_id)
                if upstream_id in hub_ids:
                    hub_fragile_spokes.append((node_id, agent_name))
                    continue
                # Collapse cycle members rather than emitting one finding per
                # cycle edge — same root cause, one record.
                scc_a = scc_membership.get(node_id)
                scc_b = scc_membership.get(upstream_id)
                if scc_a is not None and scc_a == scc_b:
                    cycle_fragile.setdefault(scc_a, []).append(
                        (node_id, agent_name, upstream_id, upstream_name)
                    )
                    continue
                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=Severity.HIGH,
                        title=f"Fragile dependency: {agent_name} relies solely on {upstream_name}",
                        description=(
                            f"Agent '{agent_name}' has a single upstream dependency on "
                            f"'{upstream_name}' with no timeout or error events recorded. "
                            f"If '{upstream_name}' responds slowly or fails, "
                            f"'{agent_name}' has no fallback."
                        ),
                        affected_agents=[node_id, upstream_id],
                        evidence={
                            "single_upstream": upstream_name,
                        },
                        remediation=(
                            f"Add a redundant upstream for '{agent_name}' or a "
                            f"timeout with a cached/default response when "
                            f"'{upstream_name}' is slow."
                        ),
                    )
                )

        # One collapsed finding per cycle SCC with >=2 fragile edges. Single-edge
        # SCCs (shouldn't really happen) re-emit individually.
        for cycle_scc, group in cycle_fragile.items():
            if len(group) < 2:
                for node_id, agent_name, upstream_id, upstream_name in group:
                    findings.append(
                        Finding(
                            test_name=self.name,
                            severity=Severity.HIGH,
                            title=f"Fragile dependency: {agent_name} relies solely on {upstream_name}",
                            description=(
                                f"Agent '{agent_name}' has a single upstream dependency "
                                f"on '{upstream_name}' with no timeout or error events "
                                f"recorded."
                            ),
                            affected_agents=[node_id, upstream_id],
                            evidence={"single_upstream": upstream_name},
                            remediation=(
                                f"Add a redundant upstream for '{agent_name}' or a "
                                f"timeout with a cached/default response."
                            ),
                        )
                    )
                continue
            member_names = sorted(graph.graph.nodes[nid].get("name", nid) for nid in cycle_scc)
            edge_strs = sorted(f"{up}→{dn}" for _, dn, _, up in group)
            sample = ", ".join(edge_strs[:5])
            if len(edge_strs) > 5:
                sample += f", … (+{len(edge_strs) - 5} more)"
            cycle_affected: list[str] = []
            seen_aff: set[str] = set()
            for node_id, _, upstream_id, _ in group:
                for aid in (upstream_id, node_id):
                    if aid not in seen_aff:
                        seen_aff.add(aid)
                        cycle_affected.append(aid)
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.HIGH,
                    title=(
                        f"Fragile dependency cycle: {', '.join(member_names)} — "
                        f"{len(group)} edges with no fallback"
                    ),
                    description=(
                        f"All {len(member_names)} members of the cycle "
                        f"({', '.join(member_names)}) have exactly one upstream — "
                        f"their cycle predecessor — and no timeout or error events "
                        f"recorded. {len(group)} fragile edges ({sample}) share the "
                        f"same root cause: the cycle. One slow member stalls all "
                        f"the others."
                    ),
                    affected_agents=cycle_affected,
                    evidence={
                        "cycle_members": member_names,
                        "fragile_edges": edge_strs,
                        "edge_count": len(group),
                        "factor": "fragile_cycle",
                    },
                    remediation=(
                        f"Break the cycle ({', '.join(member_names)}) by adding an "
                        f"exit edge or per-call timeout. Fixing the cycle closes all "
                        f"{len(group)} fragile edges at once."
                    ),
                )
            )

        # Single aggregate finding for hub fan-out (only when ≥2 spokes share
        # the same intentional hub upstream — a hub with one spoke is just an
        # ordinary edge).
        if len(hub_fragile_spokes) >= 2:
            spoke_names = sorted(name for _, name in hub_fragile_spokes)
            spoke_ids = [nid for nid, _ in hub_fragile_spokes]
            hub_id = next(iter(hub_ids))
            hub_name = graph.graph.nodes[hub_id].get("name", hub_id)
            sample = ", ".join(spoke_names[:5])
            if len(spoke_names) > 5:
                sample += f", … (+{len(spoke_names) - 5} more)"
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.INFO,
                    title=(
                        f"{len(spoke_names)} spokes have {hub_name} as sole upstream "
                        f"(intentional hub — add timeout on hub side)"
                    ),
                    description=(
                        f"{len(spoke_names)} agents ({sample}) each have '{hub_name}' as "
                        f"their only upstream. {hub_name} is the recognised intentional "
                        f"hub; this is the design. The risk remains that a slow {hub_name} "
                        f"blocks every spoke, so add timeout/retry on the hub side rather "
                        f"than per spoke."
                    ),
                    affected_agents=[hub_id] + spoke_ids,
                    evidence={
                        "hub": hub_name,
                        "spokes": spoke_names,
                        "count": len(spoke_names),
                    },
                    remediation=(
                        f"Add a per-call timeout and circuit-breaker on '{hub_name}' "
                        f"itself rather than instrumenting each spoke. One change covers "
                        f"all {len(spoke_names)} dependents."
                    ),
                )
            )

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,
            findings=findings,
            metrics=metrics,
        )
