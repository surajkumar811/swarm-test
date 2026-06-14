"""Cascade Failure Attack — simulate agent failure propagation."""

from __future__ import annotations

import logging
from typing import Any

from swarm_test.attacks.base import BaseAttack
from swarm_test.core.models import Finding, Severity, TestResult, TestStatus

logger = logging.getLogger(__name__)


class CascadeFailureAttack(BaseAttack):
    """
    Simulates a cascade failure by disabling each agent one at a time and
    measuring how many downstream agents would be impacted.

    Findings are raised when:
    - A single agent failure affects >50% of the swarm (CRITICAL)
    - A single agent failure affects >25% (HIGH)
    - A single agent failure affects >10% (MEDIUM)
    """

    name = "cascade_failure"
    description = (
        "Simulates agent failures and measures downstream propagation "
        "to detect dangerous cascade paths."
    )

    THRESHOLDS = [
        (50.0, Severity.CRITICAL, "Catastrophic cascade potential"),
        (25.0, Severity.HIGH, "High cascade risk"),
        (10.0, Severity.MEDIUM, "Moderate cascade risk"),
    ]

    def run(self, graph: Any) -> TestResult:
        findings: list[Finding] = []
        metrics: dict[str, Any] = {
            "agents_tested": 0,
            "max_impact_pct": 0.0,
            "most_critical_agent": None,
            "cascade_paths": [],
        }

        nodes = list(graph.graph.nodes())
        metrics["agents_tested"] = len(nodes)

        if len(nodes) < 2:
            return TestResult(
                test_name=self.name,
                status=TestStatus.PASSED,
                findings=[],
                metrics={"note": "Need ≥2 agents for cascade analysis"},
            )

        worst_impact = 0.0
        worst_agent = None

        for agent_id in nodes:
            blast = graph.get_blast_radius(agent_id)
            impact_pct = blast["impact_percentage"]

            if impact_pct > worst_impact:
                worst_impact = impact_pct
                worst_agent = agent_id

            downstream = blast["downstream_agents"]
            if downstream:
                metrics["cascade_paths"].append(
                    {
                        "agent": blast["agent_name"],
                        "downstream_count": len(downstream),
                        "impact_pct": impact_pct,
                    }
                )

            for threshold, severity, label in self.THRESHOLDS:
                if impact_pct >= threshold:
                    agent_name = blast["agent_name"]
                    findings.append(
                        Finding(
                            test_name=self.name,
                            severity=severity,
                            title=f"{label}: {agent_name} failure cascades to {len(downstream)} agents",
                            description=(
                                f"Agent '{agent_name}' (id={agent_id}) has a blast radius of "
                                f"{impact_pct:.1f}% — failure would directly or indirectly "
                                f"impact {len(downstream)} of {blast['total_agents']} agents."
                            ),
                            affected_agents=[agent_id] + downstream,
                            evidence=blast,
                            remediation=(
                                f"Add a fallback agent for '{agent_name}' or distribute "
                                f"its responsibilities across multiple agents."
                            ),
                        )
                    )
                    break  # Only report the highest severity per agent

        # Deduplicate findings (same agent may match multiple thresholds due to loop)
        # We break after first match, so no dedup needed.

        metrics["max_impact_pct"] = round(worst_impact, 2)
        metrics["most_critical_agent"] = (
            graph.graph.nodes[worst_agent].get("name", worst_agent)
            if worst_agent and worst_agent in graph.graph
            else None
        )

        # Sort cascade paths by impact descending
        metrics["cascade_paths"].sort(key=lambda x: x["impact_pct"], reverse=True)
        metrics["cascade_paths"] = metrics["cascade_paths"][:10]  # Top 10

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,  # overridden by probe based on findings
            findings=findings,
            metrics=metrics,
        )
