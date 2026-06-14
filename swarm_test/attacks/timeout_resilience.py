"""Timeout Resilience Attack — evaluate how agents handle slow or unresponsive peers."""

from __future__ import annotations

import logging
from typing import Any

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

        for node_id in nodes:
            in_degree = graph.graph.in_degree(node_id)
            if in_degree == 0:
                continue  # Root agent — no upstream to time out on
            if node_id not in timeout_or_error_targets:
                out_degree = graph.graph.out_degree(node_id)
                if out_degree > 0:
                    # Agent receives input and sends output but has no
                    # recorded timeout/error handling evidence.
                    metrics["agents_without_graceful_degradation"] += 1
                    agent_name = graph.graph.nodes[node_id].get("name", node_id)
                    findings.append(
                        Finding(
                            test_name=self.name,
                            severity=Severity.MEDIUM,
                            title=f"No timeout handling evidence: {agent_name}",
                            description=(
                                f"Agent '{agent_name}' has {in_degree} upstream and "
                                f"{out_degree} downstream connections but no recorded "
                                f"timeout or error events, suggesting it may not handle "
                                f"slow responses gracefully."
                            ),
                            affected_agents=[node_id],
                            evidence={
                                "in_degree": in_degree,
                                "out_degree": out_degree,
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
        for node_id in nodes:
            predecessors = list(graph.graph.predecessors(node_id))
            if len(predecessors) == 1 and node_id not in timeout_or_error_targets:
                metrics["fragile_single_path_agents"] += 1
                agent_name = graph.graph.nodes[node_id].get("name", node_id)
                upstream_name = graph.graph.nodes[predecessors[0]].get("name", predecessors[0])
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
                        affected_agents=[node_id, predecessors[0]],
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

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,
            findings=findings,
            metrics=metrics,
        )
