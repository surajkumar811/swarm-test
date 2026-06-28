"""Agent role taxonomy — auto-classify agents by their structural function.

Roles are inferred from a combination of structural metrics (in/out degree,
betweenness centrality, total degree) and lexical hints (existing role labels
and agent names). Each classification carries a 0.0-1.0 confidence value.

Roles drive role-adjusted severity: a finding's impact is interpreted
through the lens of what the agent is *supposed* to do. An orchestrator
with high blast radius is by design; a worker with high blast radius is a
design smell.
"""

from __future__ import annotations

from typing import Any

import networkx as nx


class AgentRole:
    """String constants for inferred agent roles."""

    ORCHESTRATOR = "ORCHESTRATOR"
    WORKER = "WORKER"
    VALIDATOR = "VALIDATOR"
    GATEWAY = "GATEWAY"
    AGGREGATOR = "AGGREGATOR"
    MONITOR = "MONITOR"
    ROUTER = "ROUTER"
    UNKNOWN = "UNKNOWN"


ALL_ROLES = (
    AgentRole.ORCHESTRATOR,
    AgentRole.WORKER,
    AgentRole.VALIDATOR,
    AgentRole.GATEWAY,
    AgentRole.AGGREGATOR,
    AgentRole.MONITOR,
    AgentRole.ROUTER,
    AgentRole.UNKNOWN,
)


RISK_PROFILES: dict[str, dict[str, bool]] = {
    AgentRole.ORCHESTRATOR: {
        "expected_high_blast_radius": True,
        "needs_fallback": True,
        "critical": True,
    },
    AgentRole.WORKER: {
        "expected_high_blast_radius": False,
        "needs_fallback": False,
        "critical": False,
    },
    AgentRole.VALIDATOR: {
        "expected_high_blast_radius": False,
        "needs_fallback": True,
        "security_sensitive": True,
    },
    AgentRole.GATEWAY: {
        "expected_high_blast_radius": False,
        "needs_fallback": True,
        "critical": True,
    },
    AgentRole.AGGREGATOR: {
        "expected_high_blast_radius": True,
        "needs_fallback": True,
        "critical": True,
    },
    AgentRole.MONITOR: {
        "expected_high_blast_radius": False,
        "needs_fallback": False,
        "critical": False,
    },
    AgentRole.ROUTER: {
        "expected_high_blast_radius": False,
        "needs_fallback": True,
        "critical": False,
    },
    AgentRole.UNKNOWN: {
        "expected_high_blast_radius": False,
        "needs_fallback": False,
        "critical": False,
    },
}


_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


_VALIDATOR_HINTS = ("validator", "validate", "compliance", "checker", "review", "auditor", "guard")
_MONITOR_HINTS = ("monitor", "health", "watch", "observe", "telemetry", "metric", "logger")
_ORCHESTRATOR_HINTS = (
    "orchestrator",
    "coordinator",
    "manager",
    "supervisor",
    "dispatcher",
    "planner",
)
_AGGREGATOR_HINTS = ("aggregator", "collector", "sink", "consolidator", "reducer")
_GATEWAY_HINTS = ("gateway", "entrypoint", "ingress", "egress", "boundary", "frontend", "api")
_WORKER_HINTS = ("worker", "task", "executor", "runner", "processor")
_ROUTER_HINTS = ("router", "switch", "broker", "relay")


def _declared_intentional_role(agent_id: str, agents: dict[str, Any], graph: Any) -> str | None:
    """Return the user-declared intentional role for an agent, if any.

    Looks at the AgentNode's ``intentional_role`` field first; falls back to
    a graph-node attribute of the same name (used by adapters that don't go
    through the AgentNode object). Normalises the value against ``ALL_ROLES``
    so callers don't have to.
    """
    raw: Any = None
    agent_obj = agents.get(agent_id) if isinstance(agents, dict) else None
    if agent_obj is not None:
        raw = getattr(agent_obj, "intentional_role", None)
    if not raw and graph is not None and agent_id in graph.nodes:
        raw = graph.nodes[agent_id].get("intentional_role")
    if not raw:
        return None
    candidate = str(raw).strip().upper()
    if candidate in ALL_ROLES:
        return candidate
    return None


def is_hub_role(role: str) -> bool:
    """True if a role's ``RISK_PROFILE`` expects high blast radius by design.

    Used by attacks to decide whether centrality findings on this agent are a
    real risk or an expected consequence of the role. ORCHESTRATOR and
    AGGREGATOR qualify by their RISK_PROFILES.
    """
    profile = RISK_PROFILES.get(role)
    return bool(profile and profile.get("expected_high_blast_radius"))


# Default minimum confidence for treating an inferred hub as "intentional".
# Declared intentional_role values come in at 1.0 and always clear this bar.
HUB_CONFIDENCE_THRESHOLD = 0.7


# Structural minimums for the ORCHESTRATOR role. Without these absolute
# thresholds a 4-node hub-and-spoke or a 3-node cycle trips the ratio-only
# heuristic and labels every spoke / cycle member as an orchestrator.
_HUB_MIN_OUT_DEG = 3
_HUB_BET_RATIO = 0.5


class RoleContext:
    """Per-graph role classification + helpers used by attacks.

    Built once per probe run and attached to the SwarmGraph so every attack
    sees the same classification result without redoing the centrality work.

    Splits hubs into two cohorts:
    - **intentional hubs** — declared by the user via ``intentional_role``
      (confidence = 1.0). These get full finding-suppression because the user
      explicitly accepted the design.
    - **inferred hubs** — structurally classified as orchestrator/aggregator
      above the confidence threshold but not declared. These get *severity
      downgrade* via ``role_adjusted_severity`` (one notch down) but still
      emit findings; pure inference isn't ground truth.
    """

    def __init__(self, role_map: dict[str, tuple[str, float]]) -> None:
        self.role_map: dict[str, tuple[str, float]] = dict(role_map)
        self._intentional_hubs: set[str] = {
            aid
            for aid, (role, conf) in self.role_map.items()
            if is_hub_role(role) and conf >= 0.999
        }
        self._inferred_hubs: set[str] = {
            aid
            for aid, (role, conf) in self.role_map.items()
            if (is_hub_role(role) and HUB_CONFIDENCE_THRESHOLD <= conf < 0.999)
        }

    def role_of(self, agent_id: str) -> str:
        entry = self.role_map.get(agent_id)
        return entry[0] if entry else AgentRole.UNKNOWN

    def confidence_of(self, agent_id: str) -> float:
        entry = self.role_map.get(agent_id)
        return float(entry[1]) if entry else 0.0

    def is_intentional_hub(self, agent_id: str) -> bool:
        """True for *declared* hubs only.

        Triggers full finding-suppression in attacks. Inferred-from-structure
        hubs return False here even when their confidence is high — pure
        inference is not enough to silence findings.
        """
        return agent_id in self._intentional_hubs

    def is_inferred_hub(self, agent_id: str) -> bool:
        """True for high-confidence structural orchestrators (not declared).

        Triggers severity *downgrade* via ``role_adjusted_severity`` but not
        outright suppression.
        """
        return agent_id in self._inferred_hubs

    @property
    def hubs(self) -> set[str]:
        """Union of intentional + inferred hubs — for backwards-compat callers."""
        return self._intentional_hubs | self._inferred_hubs

    @property
    def intentional_hubs(self) -> set[str]:
        return set(self._intentional_hubs)

    @property
    def inferred_hubs(self) -> set[str]:
        return set(self._inferred_hubs)


def _name_hint(agent_id: str, agents: dict[str, Any], graph: Any) -> tuple[str, str]:
    """Return (name_lower, role_lower) for hint matching."""
    name = ""
    role = ""
    agent_obj = agents.get(agent_id) if isinstance(agents, dict) else None
    if agent_obj is not None:
        name = (getattr(agent_obj, "name", "") or "").lower()
        role = (getattr(agent_obj, "role", "") or "").lower()
    if not name and graph is not None and agent_id in graph.nodes:
        data = graph.nodes[agent_id]
        name = (data.get("name", "") or "").lower()
        role = (data.get("role", "") or "").lower()
    return name, role


def _hint_match(text: str, hints: tuple[str, ...]) -> bool:
    return any(h in text for h in hints)


def classify_agent(
    agent_id: str,
    graph: Any,
    agents: dict[str, Any] | None = None,
    edges: list[Any] | None = None,
) -> tuple[str, float]:
    """
    Classify a single agent into one of the AgentRole categories.

    Returns ``(role, confidence)`` where confidence is in [0.0, 1.0].

    Uses both structural graph metrics and name/role lexical hints. When the
    agent has ``intentional_role`` set, that role is returned with confidence
    1.0 — the user's explicit declaration overrides inference.
    """
    if graph is None or agent_id not in graph.nodes:
        return AgentRole.UNKNOWN, 0.0

    agents = agents or {}

    # Honor user-declared intentional role before structural inference. A
    # declared role is treated as ground truth (confidence = 1.0) so attacks
    # can trust the hub designation without depending on the heuristic margin.
    declared = _declared_intentional_role(agent_id, agents, graph)
    if declared is not None:
        return declared, 1.0
    n = graph.number_of_nodes()
    if n <= 1:
        # Single-node graph — classify purely by name hints if any
        name, role_text = _name_hint(agent_id, agents, graph)
        for hints, role in (
            (_VALIDATOR_HINTS, AgentRole.VALIDATOR),
            (_MONITOR_HINTS, AgentRole.MONITOR),
            (_ORCHESTRATOR_HINTS, AgentRole.ORCHESTRATOR),
            (_AGGREGATOR_HINTS, AgentRole.AGGREGATOR),
            (_GATEWAY_HINTS, AgentRole.GATEWAY),
            (_WORKER_HINTS, AgentRole.WORKER),
            (_ROUTER_HINTS, AgentRole.ROUTER),
        ):
            if _hint_match(name, hints) or _hint_match(role_text, hints):
                return role, 0.6
        return AgentRole.UNKNOWN, 0.1

    # Use a simple DiGraph view for degree / centrality
    if isinstance(graph, nx.MultiDiGraph):
        simple = nx.DiGraph(graph)
    else:
        simple = graph

    in_deg = simple.in_degree(agent_id)
    out_deg = simple.out_degree(agent_id)
    total_deg = in_deg + out_deg

    try:
        centrality = nx.betweenness_centrality(simple)
    except Exception:
        centrality = {}
    betweenness = centrality.get(agent_id, 0.0)
    max_bet = max(centrality.values()) if centrality else 0.0
    bet_ratio = (betweenness / max_bet) if max_bet > 0 else 0.0

    out_ratio = out_deg / max(n - 1, 1)
    in_ratio = in_deg / max(n - 1, 1)

    name, role_text = _name_hint(agent_id, agents, graph)

    # Score every candidate role; pick the best.
    candidates: dict[str, float] = {}

    # ---------------- Structural scoring -----------------
    # ORCHESTRATOR: must look like a hub — high out-degree fan-out to many
    # distinct agents AND high betweenness. A pure spoke (out_deg=1 back to a
    # hub) or a cycle member (out_deg=in_deg=1) must NOT score as orchestrator
    # just because the graph is small and the ratio happens to clear 0.3.
    score = 0.0
    if out_deg >= _HUB_MIN_OUT_DEG and bet_ratio > _HUB_BET_RATIO:
        # Real hub: many distinct targets AND sits on most paths.
        score += 0.45
        if out_deg > in_deg:
            score += 0.15
    elif out_deg >= _HUB_MIN_OUT_DEG and out_deg > in_deg:
        # High fan-out without proven centrality (leaf workers downstream):
        # weaker signal — typical for a dispatcher with no return paths.
        score += 0.35
    elif out_deg >= 2 and in_deg == 0:
        # Pure source with multiple targets — small but unambiguous fan-out
        # (covers small graphs where the hub has no return edges yet).
        score += 0.25
    candidates[AgentRole.ORCHESTRATOR] = score

    # AGGREGATOR: must look like a sink — many distinct sources fanning IN
    # with low outgoing. A 1-in 1-out spoke is not an aggregator. Mirrors
    # the absolute-threshold fix applied to ORCHESTRATOR above.
    score = 0.0
    if in_deg >= _HUB_MIN_OUT_DEG and out_deg < in_deg:
        # Real aggregator: many sources, fewer outgoing edges.
        score += 0.45
        if in_deg > out_deg * 2:
            score += 0.15
    elif in_deg >= _HUB_MIN_OUT_DEG and out_deg <= 1:
        # Many sources fan in with at most one outgoing edge — sink-like.
        score += 0.35
    elif in_deg >= 2 and out_deg == 0:
        # Pure sink with multiple sources — small but unambiguous fan-in.
        score += 0.25
    candidates[AgentRole.AGGREGATOR] = score

    # VALIDATOR: moderate in, low out, sits in a checking position. Requires
    # in_deg >= 2 so a cycle member (in=out=1) doesn't get tagged as a
    # security-sensitive validator just because the dimensions happen to fit.
    # The lexical "validator"/"checker"/… hint below still rescues
    # genuinely-named validators with only one upstream.
    score = 0.0
    if in_deg >= 2 and out_deg <= max(1, in_deg // 2):
        score += 0.25
    if in_deg >= 2 and out_deg <= 2:
        score += 0.15
    candidates[AgentRole.VALIDATOR] = score

    # GATEWAY: at the periphery — pure source (in_deg=0) or pure sink (out_deg=0).
    score = 0.0
    if in_deg == 0 and out_deg >= 1:
        score += 0.5
    elif out_deg == 0 and in_deg >= 1:
        score += 0.45
    elif total_deg <= 2 and (in_deg == 0 or out_deg == 0):
        score += 0.3
    candidates[AgentRole.GATEWAY] = score

    # WORKER: leaf-ish, low out_degree, not a hub.
    score = 0.0
    if out_deg <= 1 and in_deg >= 1 and bet_ratio < 0.3:
        score += 0.35
    if out_ratio < 0.2 and in_ratio < 0.4:
        score += 0.15
    candidates[AgentRole.WORKER] = score

    # MONITOR: broad connections but off the critical path (low betweenness).
    score = 0.0
    if total_deg >= 2 and bet_ratio < 0.25:
        score += 0.1
    candidates[AgentRole.MONITOR] = score

    # ROUTER: balanced in/out, moderate betweenness.
    score = 0.0
    if in_deg >= 1 and out_deg >= 1:
        balance = min(in_deg, out_deg) / max(in_deg, out_deg)
        if balance >= 0.5:
            score += 0.25
        if 0.2 <= bet_ratio <= 0.7:
            score += 0.2
        if total_deg >= 3:
            score += 0.1
    candidates[AgentRole.ROUTER] = score

    # ---------------- Lexical hint boosts ----------------
    # Identity roles — validator, monitor, gateway — are almost always
    # intentional naming. Override structural signals when present.
    identity_hints = (
        (_VALIDATOR_HINTS, AgentRole.VALIDATOR),
        (_MONITOR_HINTS, AgentRole.MONITOR),
        (_GATEWAY_HINTS, AgentRole.GATEWAY),
    )
    # Functional roles — orchestrator, aggregator — get a moderate boost
    # on top of any structural signal.
    functional_hints = (
        (_ORCHESTRATOR_HINTS, AgentRole.ORCHESTRATOR),
        (_AGGREGATOR_HINTS, AgentRole.AGGREGATOR),
    )
    # Generic role labels (worker, router) only count when matched in the
    # agent's name (not its role label, which often defaults to "worker").
    weak_hints = (
        (_WORKER_HINTS, AgentRole.WORKER),
        (_ROUTER_HINTS, AgentRole.ROUTER),
    )
    for hints, role in identity_hints:
        if _hint_match(name, hints) or _hint_match(role_text, hints):
            candidates[role] = candidates.get(role, 0.0) + 0.85
    for hints, role in functional_hints:
        if _hint_match(name, hints) or _hint_match(role_text, hints):
            candidates[role] = candidates.get(role, 0.0) + 0.5
    for hints, role in weak_hints:
        if _hint_match(name, hints):
            candidates[role] = candidates.get(role, 0.0) + 0.25

    # Pick winner
    best_role = max(candidates, key=lambda r: candidates[r])
    best_score = candidates[best_role]

    if best_score < 0.2:
        return AgentRole.UNKNOWN, round(min(best_score, 0.2), 2)

    # Confidence: separation from runner-up improves confidence; cap at 0.99.
    sorted_scores = sorted(candidates.values(), reverse=True)
    runner_up = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
    margin = best_score - runner_up
    confidence = min(0.99, best_score * 0.7 + margin * 0.5 + 0.1)
    confidence = max(0.0, round(confidence, 2))

    return best_role, confidence


def classify_all(
    graph: Any,
    agents: dict[str, Any] | None = None,
    edges: list[Any] | None = None,
) -> dict[str, tuple[str, float]]:
    """Classify every agent in the graph. Returns ``{agent_id: (role, confidence)}``."""
    if graph is None:
        return {}
    out: dict[str, tuple[str, float]] = {}
    for nid in graph.nodes():
        out[nid] = classify_agent(nid, graph, agents, edges)
    return out


def role_adjusted_severity(role: str, finding_type: str, base_severity: str) -> str:
    """
    Adjust a finding's severity based on the agent's role.

    Examples of the heuristics applied:

    - An orchestrator/aggregator with a high blast radius is *by design* —
      downgrade severity by one level.
    - A worker with a high blast radius is a design smell — keep severity.
    - A validator with context leakage is security-sensitive — upgrade severity.
    - A gateway with cascade exposure is critical — upgrade severity.
    """
    sev = (base_severity or "").lower()
    if sev not in _SEVERITY_ORDER:
        return base_severity

    profile = RISK_PROFILES.get(role, RISK_PROFILES[AgentRole.UNKNOWN])
    idx = _SEVERITY_ORDER.index(sev)
    ftype = (finding_type or "").lower()

    # Downgrade: roles where high blast radius is expected
    if ftype in {"blast_radius", "cascade_failure", "cascade"} and profile.get(
        "expected_high_blast_radius"
    ):
        new_idx = min(len(_SEVERITY_ORDER) - 1, idx + 1)
        return _SEVERITY_ORDER[new_idx]

    # Upgrade: validators leaking context, gateways under cascade
    if ftype in {"context_leakage", "leakage", "sensitive_data"} and profile.get(
        "security_sensitive"
    ):
        new_idx = max(0, idx - 1)
        return _SEVERITY_ORDER[new_idx]

    if (
        ftype in {"cascade_failure", "cascade", "blast_radius"}
        and profile.get("critical")
        and role == AgentRole.GATEWAY
    ):
        new_idx = max(0, idx - 1)
        return _SEVERITY_ORDER[new_idx]

    # Worker with high blast radius — keep (do not downgrade)
    return sev
