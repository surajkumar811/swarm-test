"""Example plugin implementation."""

from __future__ import annotations

from swarm_test.core.models import Finding, Severity
from swarm_test.plugins import BasePlugin, PluginResult


class ExamplePlugin(BasePlugin):
    """Flag swarms with fewer agents than a configurable minimum."""

    name = "example"
    version = "0.1.0"
    description = "Example plugin — flags swarms with fewer than 2 agents"
    author = "your-name"

    MIN_AGENTS = 2

    def run(self, graph, agents, edges, config) -> PluginResult:
        findings: list[Finding] = []
        agent_count = graph.graph.number_of_nodes()

        if agent_count < self.MIN_AGENTS:
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.MEDIUM,
                    title=f"Swarm has only {agent_count} agent(s)",
                    description=(
                        f"This swarm contains {agent_count} agent(s), which is below "
                        f"the configured minimum of {self.MIN_AGENTS}."
                    ),
                    affected_agents=[],
                    remediation="Add more agents to the swarm or remove the check.",
                )
            )

        return PluginResult(
            test_name=self.name,
            status="passed" if not findings else "failed",
            score=100.0 if not findings else 50.0,
            findings=findings,
            duration_ms=0.0,
        )
