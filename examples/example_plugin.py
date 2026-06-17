"""Example swarm-test plugin: edge_count_check.

A minimal plugin that warns when an agent has too many outgoing edges.
Use it as a starting template for your own custom reliability tests.

To install this plugin so swarm-test auto-discovers it:

  1. Add to your project's pyproject.toml:

       [project.entry-points."swarm_test.plugins"]
       edge_count_check = "my_package.plugins:EdgeCountPlugin"

  2. pip install -e .

  3. swarm-test plugins list   # should show edge_count_check
"""

from __future__ import annotations

from swarm_test.core.models import Finding, Severity
from swarm_test.plugins import BasePlugin, PluginResult


class EdgeCountPlugin(BasePlugin):
    """Warn when any agent has more than N outgoing edges."""

    name = "edge_count_check"
    version = "0.1.0"
    description = "Warns if any agent has more than N outgoing edges"
    author = "swarm-test contributors"

    HIGH_THRESHOLD = 5
    CRITICAL_THRESHOLD = 10

    def run(self, graph, agents, edges, config) -> PluginResult:
        findings: list[Finding] = []
        overloaded = 0

        for agent_id in graph.graph.nodes():
            out_count = graph.graph.out_degree(agent_id)
            if out_count <= self.HIGH_THRESHOLD:
                continue

            agent_name = graph.graph.nodes[agent_id].get("name", agent_id)
            overloaded += 1

            if out_count > self.CRITICAL_THRESHOLD:
                severity = Severity.CRITICAL
                title = (
                    f"Agent '{agent_name}' has {out_count} outgoing edges — "
                    f"split responsibilities now"
                )
            else:
                severity = Severity.HIGH
                title = (
                    f"Agent '{agent_name}' has {out_count} outgoing edges — "
                    f"consider splitting responsibilities"
                )

            findings.append(
                Finding(
                    test_name=self.name,
                    severity=severity,
                    title=title,
                    description=(
                        f"Agent '{agent_name}' coordinates {out_count} downstream "
                        f"agents. High fan-out concentrates failure impact and "
                        f"makes the agent a likely bottleneck."
                    ),
                    affected_agents=[agent_id],
                    evidence={
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "outgoing_edges": out_count,
                        "threshold": self.HIGH_THRESHOLD,
                    },
                    remediation=(
                        f"Introduce intermediate router agents so '{agent_name}' "
                        f"delegates to no more than {self.HIGH_THRESHOLD} peers."
                    ),
                )
            )

        score = max(0.0, 100.0 - overloaded * 10)
        status = "passed" if not findings else "failed"
        return PluginResult(
            test_name=self.name,
            status=status,
            score=score,
            findings=findings,
            duration_ms=0.0,
        )
