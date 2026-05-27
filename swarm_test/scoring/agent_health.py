"""Per-agent health scoring — each agent gets a 0-100 reliability rating."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


@dataclass
class AgentHealthScore:
    """Health score breakdown for a single agent."""

    agent_id: str
    agent_name: str
    role: str
    score: int  # 0-100
    reasons: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def status_icon(self) -> str:
        if self.score >= 70:
            return "\u2705"  # check mark
        if self.score >= 40:
            return "\u26a0\ufe0f"  # warning
        return "\u274c"  # cross

    @property
    def status_label(self) -> str:
        if self.score >= 70:
            return "healthy"
        if self.score >= 40:
            return "moderate risk"
        return "critical"


class AgentHealthScorer:
    """Calculates a 0-100 health score for each agent in a SwarmGraph."""

    def score_all(self, graph: Any) -> dict[str, AgentHealthScore]:
        """Return agent_id → AgentHealthScore for every agent in the graph."""
        g = graph.graph
        if g.number_of_nodes() == 0:
            return {}

        # Pre-compute graph-level data once
        spofs = set(graph.find_single_points_of_failure())
        critical_path = graph.get_critical_path()
        cliques = self._find_cliques(g)
        clique_membership = self._clique_membership(cliques)

        scores: dict[str, AgentHealthScore] = {}
        for agent_id in g.nodes():
            data = g.nodes[agent_id]
            name = data.get("name", agent_id)
            role = data.get("role", "unknown")

            score = 100
            reasons: list[str] = []
            breakdown: dict[str, int] = {}

            # 1. Blast radius penalty (0 to -40)
            blast = graph.get_blast_radius(agent_id)
            impact_pct = blast["impact_percentage"]
            blast_penalty = int(impact_pct * 0.4)
            if blast_penalty > 0:
                score -= blast_penalty
                breakdown["blast_radius"] = -blast_penalty
                reasons.append(f"{impact_pct:.0f}% blast radius")

            # 2. SPOF penalty (-30)
            if agent_id in spofs:
                score -= 30
                breakdown["spof"] = -30
                reasons.append("SPOF")

            # 3. Cascade depth penalty (position on critical path, up to -20)
            if agent_id in critical_path:
                pos = critical_path.index(agent_id)
                depth_ratio = pos / max(len(critical_path) - 1, 1)
                depth_penalty = int(depth_ratio * 20)
                if depth_penalty > 0:
                    score -= depth_penalty
                    breakdown["cascade_depth"] = -depth_penalty
                    reasons.append("high cascade depth")

            # 4. Collusion clique penalty (-10 per clique)
            agent_cliques = clique_membership.get(agent_id, [])
            if agent_cliques:
                clique_penalty = min(len(agent_cliques) * 10, 30)
                score -= clique_penalty
                breakdown["collusion_cliques"] = -clique_penalty
                reasons.append(f"{len(agent_cliques)} collusion clique(s)")

            # 5. Timeout resilience — fallback bonus (+10 if multiple upstreams)
            in_degree = g.in_degree(agent_id)
            out_degree = g.out_degree(agent_id)
            if in_degree >= 2:
                score += 10
                breakdown["fallback_bonus"] = 10
                reasons.append("has fallback upstreams")

            # 6. Edge ratio imbalance penalty (up to -15)
            total_degree = in_degree + out_degree
            if total_degree > 0:
                ratio = min(in_degree, out_degree) / max(in_degree, out_degree, 1)
                if ratio < 0.2 and total_degree >= 3:
                    imbalance_penalty = 15
                    score -= imbalance_penalty
                    breakdown["edge_imbalance"] = -imbalance_penalty
                    reasons.append("lopsided edge ratio")
                elif ratio < 0.5 and total_degree >= 3:
                    imbalance_penalty = 8
                    score -= imbalance_penalty
                    breakdown["edge_imbalance"] = -imbalance_penalty
                    reasons.append("unbalanced edges")

            score = max(0, min(100, score))

            scores[agent_id] = AgentHealthScore(
                agent_id=agent_id,
                agent_name=name,
                role=role,
                score=score,
                reasons=reasons,
                breakdown=breakdown,
            )

        return scores

    @staticmethod
    def _find_cliques(g: nx.MultiDiGraph) -> list[list[str]]:
        """Find cliques of size >= 3 on the undirected projection."""
        try:
            undirected = g.to_undirected(as_view=True)
            return [c for c in nx.find_cliques(undirected) if len(c) >= 3]
        except nx.NetworkXError:
            return []

    @staticmethod
    def _clique_membership(cliques: list[list[str]]) -> dict[str, list[int]]:
        """Map agent_id → list of clique indices it belongs to."""
        membership: dict[str, list[int]] = {}
        for i, clique in enumerate(cliques):
            for agent_id in clique:
                membership.setdefault(agent_id, []).append(i)
        return membership
