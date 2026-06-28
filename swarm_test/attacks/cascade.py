"""Cascade Failure Attack — simulate agent failure propagation."""

from __future__ import annotations

import logging
from typing import Any

from swarm_test.attacks.base import BaseAttack
from swarm_test.core.models import Finding, Severity, TestResult, TestStatus
from swarm_test.core.taxonomy import role_adjusted_severity

logger = logging.getLogger(__name__)


_SEVERITY_FROM_STR = {s.value: s for s in Severity}
_REPORTABLE = {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}


class CascadeFailureAttack(BaseAttack):
    """
    Simulates a cascade failure by disabling each agent one at a time and
    measuring how many downstream agents would be impacted.

    Findings are raised when:
    - A single agent failure affects >50% of the swarm (CRITICAL)
    - A single agent failure affects >25% (HIGH)
    - A single agent failure affects >10% (MEDIUM)

    Role-aware behavior:
    - Per-agent thresholds use the *effective* blast radius (descendants that
      don't depend on the agent only through an intentional hub). A worker
      whose only "downstream" runs back through the orchestrator stops being
      counted as a hub-scale risk.
    - The raw severity is filtered through ``role_adjusted_severity``.
      Roles whose RISK_PROFILE expects high blast radius (orchestrator,
      aggregator) get downgraded, and per-spec we suppress the per-agent
      finding entirely for high-confidence intentional hubs — a single
      "centralised topology" finding collapses the fan-out instead.
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

        role_ctx = getattr(graph, "role_context", None)
        # Two hub sets are tracked separately:
        # - ``intentional_hubs`` (declared) trigger full per-agent suppression
        #   and roll into a single "centralised topology" record.
        # - ``effective_hubs`` (declared + inferred) drive the spoke blast
        #   computation. A spoke whose only downstream runs through the
        #   *inferred* orchestrator inherits a fake 100% reach if we exclude
        #   only declared hubs — so use the union for the reachability
        #   trimming, but keep severity downgrade (not suppression) for the
        #   inferred case via role_adjusted_severity.
        intentional_hubs: set[str] = (
            role_ctx.intentional_hubs if role_ctx is not None else set()
        )
        effective_hubs: set[str] = (
            role_ctx.hubs if role_ctx is not None else set()
        )

        worst_impact = 0.0
        worst_agent = None

        # Track per-severity agents whose finding was role-suppressed. Used to
        # emit a single collapsed "centralised topology" finding instead of N
        # per-spoke CRITICAL findings.
        suppressed_by_hub: dict[str, list[str]] = {}
        per_agent_findings: list[Finding] = []

        for agent_id in nodes:
            # Raw blast radius drives the metric (preserves backward-compat
            # for tests that read max_impact_pct).
            raw = graph.get_blast_radius(agent_id)
            raw_impact_pct = raw["impact_percentage"]

            if raw_impact_pct > worst_impact:
                worst_impact = raw_impact_pct
                worst_agent = agent_id

            raw_downstream = raw["downstream_agents"]
            if raw_downstream:
                metrics["cascade_paths"].append(
                    {
                        "agent": raw["agent_name"],
                        "downstream_count": len(raw_downstream),
                        "impact_pct": raw_impact_pct,
                    }
                )

            # Effective blast radius drives the *finding* — descendants that
            # don't depend on this agent only through a hub. Use the union of
            # declared and inferred hubs so spokes around an inferred
            # orchestrator don't inherit the hub's reach.
            effective = graph.get_effective_blast_radius(agent_id, effective_hubs)
            impact_pct = effective["impact_percentage"]
            downstream = effective["downstream_agents"]

            agent_role = role_ctx.role_of(agent_id) if role_ctx is not None else ""

            for threshold, severity, label in self.THRESHOLDS:
                if impact_pct < threshold:
                    continue
                # Role-adjusted severity. role_adjusted_severity downgrades
                # blast_radius/cascade findings on hub-roles by one level.
                adjusted_str = role_adjusted_severity(
                    agent_role, "cascade_failure", severity.value
                )
                adjusted = _SEVERITY_FROM_STR.get(adjusted_str, severity)

                # For *declared* intentional hubs the centrality is
                # design-intent — suppress the per-agent finding and roll the
                # agent into the collapsed "centralised topology" record.
                # Inferred-only hubs still emit (with downgraded severity).
                if agent_id in intentional_hubs:
                    suppressed_by_hub.setdefault(severity.value, []).append(agent_id)
                    break

                # If the role downgrade pushes the finding below MEDIUM,
                # don't emit it (LOW/INFO would just be report noise).
                if adjusted not in _REPORTABLE:
                    suppressed_by_hub.setdefault(severity.value, []).append(agent_id)
                    break

                agent_name = effective["agent_name"]
                per_agent_findings.append(
                    Finding(
                        test_name=self.name,
                        severity=adjusted,
                        title=f"{label}: {agent_name} failure cascades to {len(downstream)} agents",
                        description=(
                            f"Agent '{agent_name}' has a blast radius of "
                            f"{impact_pct:.1f}% — failure would directly or indirectly "
                            f"impact {len(downstream)} of {effective['total_agents']} agents."
                        ),
                        affected_agents=[agent_id] + downstream,
                        evidence={
                            **effective,
                            "raw_impact_percentage": raw_impact_pct,
                            "role": agent_role,
                            "role_confidence": (
                                role_ctx.confidence_of(agent_id) if role_ctx else 0.0
                            ),
                        },
                        remediation=(
                            f"Add a fallback agent for '{agent_name}' or distribute "
                            f"its responsibilities across multiple agents."
                        ),
                    )
                )
                break  # Only report the highest severity per agent

        # Collapse per-spoke CRITICALs of the same hub into a single finding.
        # Per-agent CRITICAL findings for distinct downstream sets stay
        # individual; spokes that share an intentional hub are deduplicated.
        findings.extend(self._collapse_per_agent_findings(per_agent_findings))

        # Single informational finding for hub-and-spoke topologies. Even
        # though the per-agent CRITICAL findings are gone, the report still
        # surfaces the centralised topology so the user can see the design.
        hub_finding = self._build_hub_topology_finding(
            graph, intentional_hubs, suppressed_by_hub, worst_impact
        )
        if hub_finding is not None:
            findings.append(hub_finding)

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _collapse_per_agent_findings(
        self, per_agent: list[Finding]
    ) -> list[Finding]:
        """Collapse multiple per-agent findings that describe the same
        architectural fact into a single multi-agent finding.

        Two collapse-keys are tried, in order:
        1. **Same total reach** — ``frozenset(affected_agents)`` matches. This
           catches cycle members: in an A→B→C→A cycle, A/B/C each emit a
           finding whose root+downstream is the same {A,B,C} set, just rooted
           differently. Same root cause, one finding.
        2. **Same downstream set** — ``sorted(affected_agents[1:])`` matches.
           Catches the hub-spoke case where N siblings share the same
           downstream reach but have distinct roots.

        Findings whose total agent set is unique stay individual so genuine
        cross-component cascades remain actionable.
        """
        groups: dict[tuple[str, tuple[str, ...]], list[Finding]] = {}
        for f in per_agent:
            # affected_agents = [self_id, *downstream]; collapse on the
            # FULL set so a cycle's per-root findings (each rooted at a
            # different cycle member) end up in one bucket.
            full_key = tuple(sorted(set(f.affected_agents)))
            key = (f.severity.value, full_key)
            groups.setdefault(key, []).append(f)

        collapsed: list[Finding] = []
        for group in groups.values():
            if len(group) == 1:
                collapsed.append(group[0])
                continue
            # Collapse: merge affected_agents and rewrite the title/desc.
            ranked = sorted(group, key=lambda x: x.title)
            primary = ranked[0]
            all_agents: list[str] = []
            agent_names: list[str] = []
            seen_ids: set[str] = set()
            for f in ranked:
                for aid in f.affected_agents:
                    if aid not in seen_ids:
                        seen_ids.add(aid)
                        all_agents.append(aid)
                # First element of affected_agents is the root agent;
                # extract a readable name from evidence when present.
                aname = f.evidence.get("agent_name") if f.evidence else None
                if isinstance(aname, str) and aname not in agent_names:
                    agent_names.append(aname)
            sample = ", ".join(agent_names[:5])
            if len(agent_names) > 5:
                sample += f", … (+{len(agent_names) - 5} more)"
            count = len(group)
            collapsed.append(
                Finding(
                    test_name=primary.test_name,
                    severity=primary.severity,
                    title=(
                        f"{count} agents share the same cascade reach — "
                        f"{primary.severity.value.upper()} blast radius"
                    ),
                    description=(
                        f"{count} agents ({sample}) have the same downstream set "
                        f"and the same effective blast radius. This is one "
                        f"structural fact (a shared dependency) seen from "
                        f"{count} vantage points — treat it as a single "
                        f"design issue, not {count} independent risks."
                    ),
                    affected_agents=all_agents,
                    evidence={
                        "collapsed_from": count,
                        "shared_downstream": list(group[0].affected_agents[1:]),
                        "severity": primary.severity.value,
                    },
                    remediation=primary.remediation,
                )
            )
        return collapsed

    def _build_hub_topology_finding(
        self,
        graph: Any,
        hub_ids: set[str],
        suppressed_by_hub: dict[str, list[str]],
        worst_impact: float,
    ) -> Finding | None:
        """Emit one informational finding describing the hub-and-spoke topology.

        Only emitted when an intentional hub absorbed at least one suppression
        (otherwise the topology is something other than hub-and-spoke or the
        hub is so small there's nothing to report). The finding records:
        - which agent is the recognised hub,
        - how many spokes share its centrality,
        - that the per-spoke CRITICAL/HIGH findings are by-design.
        """
        if not hub_ids or not suppressed_by_hub:
            return None
        suppressed_ids: set[str] = set()
        for ids in suppressed_by_hub.values():
            suppressed_ids.update(ids)
        # Only the hub itself was suppressed (no spokes piggy-backed) → no
        # extra context to add beyond the existing per-finding output.
        spoke_count = len(suppressed_ids - hub_ids)
        if spoke_count == 0:
            return None

        hub_names = sorted(
            graph.graph.nodes[h].get("name", h)
            for h in hub_ids
            if h in graph.graph
        )
        spoke_names = sorted(
            graph.graph.nodes[s].get("name", s)
            for s in suppressed_ids
            if s in graph.graph and s not in hub_ids
        )
        hub_label = ", ".join(hub_names) if hub_names else "the inferred hub"

        return Finding(
            test_name=self.name,
            severity=Severity.INFO,
            title=(
                f"Centralised topology around {hub_label} — "
                f"{len(spoke_names)} spoke(s) share its blast radius by design"
            ),
            description=(
                f"{hub_label} is the recognised intentional hub for this swarm. "
                f"{len(spoke_names)} agents inherit its centrality because every "
                f"request flows through it. Their individual cascade findings "
                f"were collapsed into this single record — the design is "
                f"working as intended, but losing {hub_label} still loses the "
                f"swarm (max raw blast radius: {worst_impact:.1f}%). Add a "
                f"warm standby or partition the workload to bound the risk."
            ),
            affected_agents=list(hub_ids) + sorted(suppressed_ids - hub_ids),
            evidence={
                "hubs": hub_names,
                "suppressed_spoke_count": len(spoke_names),
                "raw_max_impact_percentage": worst_impact,
                "suppressed_by_severity": {
                    sev: sorted(
                        graph.graph.nodes[aid].get("name", aid)
                        for aid in ids
                        if aid in graph.graph
                    )
                    for sev, ids in suppressed_by_hub.items()
                },
            },
            remediation=(
                f"Hub-and-spoke topology around {hub_label} is intentional. "
                f"Add a hot standby or split the workload across two hubs if "
                f"availability matters more than simplicity."
            ),
        )
