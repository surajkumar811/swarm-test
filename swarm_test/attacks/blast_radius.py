"""Blast Radius Attack — quantify impact of targeted agent failures."""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from swarm_test.attacks.base import BaseAttack
from swarm_test.core.models import Finding, Severity, TestResult, TestStatus

logger = logging.getLogger(__name__)


class BlastRadiusAttack(BaseAttack):
    """
    Performs a systematic blast radius analysis across the entire swarm:
    - Identifies agents whose failure would most impact the system
    - Detects single points of failure (articulation points)
    - Quantifies the critical path and its length
    - Checks for adequate redundancy

    This is a quantitative complement to CascadeFailureAttack,
    focusing on topology-level metrics rather than simulation.
    """

    name = "blast_radius"
    description = (
        "Topological blast radius analysis: identifies critical agents, "
        "single points of failure, and lack of redundancy in the swarm graph."
    )

    def run(self, graph: Any) -> TestResult:
        findings: list[Finding] = []
        metrics: dict[str, Any] = {
            "total_agents": 0,
            "total_edges": 0,
            "single_points_of_failure": [],
            "critical_path": [],
            "critical_path_length": 0,
            "top_blast_agents": [],
            "redundancy_score": 0.0,
            "graph_density": 0.0,
        }

        g = graph.graph
        n = g.number_of_nodes()
        e = g.number_of_edges()
        metrics["total_agents"] = n
        metrics["total_edges"] = e

        if n < 2:
            return TestResult(
                test_name=self.name,
                status=TestStatus.PASSED,
                findings=[],
                metrics={"note": "Need ≥2 agents for blast radius analysis"},
            )

        # Graph density
        density = nx.density(g)
        metrics["graph_density"] = round(density, 4)

        # 1. Single Points of Failure (articulation points)
        spofs = graph.find_single_points_of_failure()
        metrics["single_points_of_failure"] = [g.nodes[s].get("name", s) for s in spofs if s in g]

        for spof_id in spofs:
            if spof_id not in g:
                continue
            spof_name = g.nodes[spof_id].get("name", spof_id)
            blast = graph.get_blast_radius(spof_id)
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.CRITICAL,
                    title=f"Single Point of Failure: {spof_name}",
                    description=(
                        f"Agent '{spof_name}' is an articulation point — removing it "
                        f"would disconnect the agent communication graph. "
                        f"Blast radius: {blast['impact_percentage']:.1f}% of agents affected."
                    ),
                    affected_agents=[spof_id] + blast["downstream_agents"],
                    evidence={
                        "agent_id": spof_id,
                        "impact_percentage": blast["impact_percentage"],
                        "downstream_count": len(blast["downstream_agents"]),
                    },
                    remediation=(
                        f"Reduce '{spof_name}' connections by introducing intermediate "
                        f"routing agents, or replicate '{spof_name}' so traffic can "
                        f"fail over to a peer."
                    ),
                )
            )

        # 2. Critical path analysis
        critical_path = graph.get_critical_path()
        metrics["critical_path"] = [g.nodes[n].get("name", n) for n in critical_path if n in g]
        metrics["critical_path_length"] = len(critical_path)

        if len(critical_path) >= 4:
            path_names = metrics["critical_path"]
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.HIGH,
                    title=f"Long critical path: {len(critical_path)} agents",
                    description=(
                        f"The critical path spans {len(critical_path)} agents: "
                        f"{' → '.join(path_names)}. "
                        "Failure anywhere on this path creates a service outage."
                    ),
                    affected_agents=critical_path,
                    evidence={"path": critical_path, "path_names": path_names},
                    remediation=(
                        f"Shorten the critical path '{' → '.join(path_names)}' by "
                        f"parallelising independent agents and adding checkpoint/retry "
                        f"between '{path_names[0]}' and '{path_names[-1]}'."
                    ),
                )
            )

        # 3. Top blast radius agents
        blast_scores = []
        for node in g.nodes():
            blast = graph.get_blast_radius(node)
            blast_scores.append(
                {
                    "agent_id": node,
                    "agent_name": g.nodes[node].get("name", node),
                    "impact_pct": blast["impact_percentage"],
                    "downstream_count": len(blast["downstream_agents"]),
                }
            )
        blast_scores.sort(key=lambda x: x["impact_pct"], reverse=True)
        metrics["top_blast_agents"] = blast_scores[:5]

        # 4. Redundancy score — ratio of edges to minimum spanning tree edges
        # Higher = more redundant paths
        undirected = g.to_undirected()
        if undirected.number_of_edges() > 0 and nx.is_connected(undirected):
            mst_edges = undirected.number_of_nodes() - 1
            actual_edges = undirected.number_of_edges()
            redundancy = (actual_edges - mst_edges) / max(mst_edges, 1)
            metrics["redundancy_score"] = round(redundancy, 3)

            if redundancy < 0.1 and n > 3:
                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=Severity.MEDIUM,
                        title=f"Low redundancy score: {redundancy:.2f}",
                        description=(
                            f"The swarm graph has a redundancy score of {redundancy:.2f} "
                            "(close to a tree structure with no alternative paths). "
                            "A single edge failure could create an unreachable agent."
                        ),
                        affected_agents=list(g.nodes()),
                        evidence={"redundancy_score": redundancy, "edge_count": actual_edges},
                        remediation=(
                            f"Add fallback edges so any single edge failure still "
                            f"leaves the swarm connected — current redundancy score "
                            f"is {redundancy:.2f}; aim for ≥ 0.30."
                        ),
                    )
                )

        # 5. Isolated agents (zero in-degree AND zero out-degree, excluding root)
        isolated = [n for n in g.nodes() if g.in_degree(n) == 0 and g.out_degree(n) == 0]
        if isolated:
            isolated_names = [g.nodes[i].get("name", i) for i in isolated]
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.LOW,
                    title=f"{len(isolated)} isolated agent(s) detected",
                    description=(
                        f"Agents {isolated_names} have no connections to any other agent. "
                        "They will never be tested under load and may represent dead code."
                    ),
                    affected_agents=isolated,
                    evidence={"isolated_agents": isolated_names},
                    remediation=(
                        f"Remove unused agents {isolated_names} or wire them into the "
                        f"swarm workflow with at least one upstream or downstream edge."
                    ),
                )
            )

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,
            findings=findings,
            metrics=metrics,
        )
