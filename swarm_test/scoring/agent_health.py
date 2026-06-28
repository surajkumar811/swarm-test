"""Per-agent health scoring — each agent gets a 0-100 reliability rating."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from swarm_test.core.taxonomy import AgentRole, role_adjusted_severity

logger = logging.getLogger(__name__)


# Maps a severity bucket back to a penalty share. Lets us model
# role_adjusted_severity's "one notch down" behaviour as a penalty haircut
# (critical → 100%, high → 50%, medium → 25%, anything lower → 0%).
_SEV_PENALTY_SHARE: dict[str, float] = {
    "critical": 1.0,
    "high": 0.5,
    "medium": 0.25,
    "low": 0.0,
    "info": 0.0,
}


def _impact_to_severity(pct: float) -> str:
    """Convert a blast-radius impact percentage to a severity bucket.

    Mirrors the bucket thresholds CascadeFailureAttack uses so the two
    surfaces (attack findings + health scoring) agree on what counts as
    critical / high / medium centrality.
    """
    if pct >= 50.0:
        return "critical"
    if pct >= 25.0:
        return "high"
    if pct >= 10.0:
        return "medium"
    if pct > 0:
        return "low"
    return "info"


@dataclass
class AgentHealthScore:
    """Health score breakdown for a single agent."""

    agent_id: str
    agent_name: str
    role: str
    score: int  # 0-100
    reasons: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)
    redundancy_score: float = 0.0  # 0 = irreplaceable, 100 = fully redundant

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
        """Return agent_id → AgentHealthScore for every agent in the graph.

        Role-aware:
        - Spokes use ``get_effective_blast_radius`` so descendants reachable
          only through an intentional/inferred hub don't inflate their score.
        - Hub-role agents (orchestrator/aggregator) have their centrality
          penalties (blast radius, SPOF, cascade depth, edge imbalance)
          downgraded via ``role_adjusted_severity`` — by-design centrality
          shouldn't drag a healthy hub to a critical score.
        - Declared intentional hubs get full suppression of centrality
          penalties, mirroring the cascade attack. Their health then reflects
          only *real* issues (collusion cliques, missing fallbacks, etc.)
          instead of expected hub behaviour.
        """
        g = graph.graph
        if g.number_of_nodes() == 0:
            return {}

        # Pre-compute graph-level data once
        spofs = set(graph.find_single_points_of_failure())
        critical_path = graph.get_critical_path()
        cliques = self._find_cliques(g)
        clique_membership = self._clique_membership(cliques)

        role_ctx = getattr(graph, "role_context", None)
        intentional_hubs: set[str] = role_ctx.intentional_hubs if role_ctx is not None else set()
        inferred_hubs: set[str] = role_ctx.inferred_hubs if role_ctx is not None else set()
        hubs: set[str] = intentional_hubs | inferred_hubs

        scores: dict[str, AgentHealthScore] = {}
        for agent_id in g.nodes():
            data = g.nodes[agent_id]
            name = data.get("name", agent_id)
            role = data.get("role", "unknown")

            is_intentional_hub = agent_id in intentional_hubs
            is_inferred_hub = agent_id in inferred_hubs
            is_hub = is_intentional_hub or is_inferred_hub

            # The role the *severity adjustment layer* sees — falls back to
            # UNKNOWN when role classification didn't run (legacy callers).
            sev_role = role_ctx.role_of(agent_id) if role_ctx is not None else AgentRole.UNKNOWN

            # Centrality multiplier mirrors the cascade attack's behaviour:
            #   intentional hub → 0   (suppressed, by-design centrality)
            #   inferred hub    → 0.5 (one-notch downgrade)
            #   otherwise       → 1.0
            if is_intentional_hub:
                centrality_mul = 0.0
            elif is_inferred_hub:
                centrality_mul = 0.5
            else:
                centrality_mul = 1.0

            score = 100
            reasons: list[str] = []
            breakdown: dict[str, int] = {}

            # 1. Blast radius penalty (0 to -40)
            # Use the hub-excluding effective radius for spokes so a leaf that
            # only returns to the orchestrator doesn't inherit the hub's reach.
            # Hubs themselves still report their raw blast radius (their actual
            # design-impact) but the penalty is scaled by centrality_mul.
            if role_ctx is not None and not is_hub:
                blast = graph.get_effective_blast_radius(agent_id, hubs)
            else:
                blast = graph.get_blast_radius(agent_id)
            impact_pct = blast["impact_percentage"]

            # Apply role_adjusted_severity to mirror the cascade attack's
            # severity downgrade. We compute the would-be severity of this
            # impact, run it through the role layer, and translate the result
            # back to a penalty share. This guarantees the agent_health surface
            # agrees with cascade findings on what counts as critical centrality.
            base_blast_sev = _impact_to_severity(impact_pct)
            adj_blast_sev = role_adjusted_severity(sev_role, "blast_radius", base_blast_sev)
            sev_share = _SEV_PENALTY_SHARE.get(adj_blast_sev, 0.0)
            blast_penalty = int(impact_pct * 0.4 * sev_share * centrality_mul)
            if blast_penalty > 0:
                score -= blast_penalty
                breakdown["blast_radius"] = -blast_penalty
                reasons.append(f"{impact_pct:.0f}% blast radius")
            elif is_intentional_hub and impact_pct > 0:
                reasons.append(f"{impact_pct:.0f}% blast radius (by-design hub)")

            # 2. SPOF penalty (-30)
            if agent_id in spofs:
                spof_penalty = int(30 * centrality_mul)
                if spof_penalty > 0:
                    score -= spof_penalty
                    breakdown["spof"] = -spof_penalty
                    reasons.append("SPOF")
                elif is_intentional_hub:
                    reasons.append("SPOF (by-design hub)")

            # 3. Cascade depth penalty (position on critical path, up to -20)
            if agent_id in critical_path:
                pos = critical_path.index(agent_id)
                depth_ratio = pos / max(len(critical_path) - 1, 1)
                depth_penalty = int(depth_ratio * 20 * centrality_mul)
                if depth_penalty > 0:
                    score -= depth_penalty
                    breakdown["cascade_depth"] = -depth_penalty
                    reasons.append("high cascade depth")

            # 4. Collusion clique penalty (-10 per clique). NOT a centrality
            # signal — keep at full strength even for hubs.
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

            # 6. Edge ratio imbalance penalty (up to -15). Hubs are
            # *expected* to be lopsided — scale by centrality_mul.
            total_degree = in_degree + out_degree
            if total_degree > 0 and centrality_mul > 0:
                ratio = min(in_degree, out_degree) / max(in_degree, out_degree, 1)
                if ratio < 0.2 and total_degree >= 3:
                    imbalance_penalty = int(15 * centrality_mul)
                    if imbalance_penalty > 0:
                        score -= imbalance_penalty
                        breakdown["edge_imbalance"] = -imbalance_penalty
                        reasons.append("lopsided edge ratio")
                elif ratio < 0.5 and total_degree >= 3:
                    imbalance_penalty = int(8 * centrality_mul)
                    if imbalance_penalty > 0:
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
