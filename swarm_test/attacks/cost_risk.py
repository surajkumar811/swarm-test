"""Cost Risk Analysis — static, relative token-waste risk scoring.

Reads structural risk signals already detectable from the graph and assigns each
a relative cost-waste weight. NO runtime data, NO LLM calls, NO dollar amounts —
this is a free static estimate that flags topology patterns most likely to burn
tokens (unbounded loops, retry-prone fragile dependencies, long critical paths,
high fan-out nodes). Real per-run cost measurement requires execution data and
is intentionally out of scope here.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from swarm_test.attacks.base import BaseAttack
from swarm_test.core.models import Finding, Severity, TestResult, TestStatus

logger = logging.getLogger(__name__)

# Score thresholds for the 0-100 Cost Risk Score. Each tuple is
# (inclusive_upper_bound, label). Bands: 0–24 LOW, 25–49 MODERATE,
# 50–74 HIGH, 75–100 SEVERE. The severity floors below (CRITICAL→75,
# HIGH→50, MEDIUM→25) land exactly at these band starts so the score
# number and the verdict word always agree.
_VERDICT_BANDS: tuple[tuple[int, str], ...] = (
    (24, "LOW"),
    (49, "MODERATE"),
    (74, "HIGH"),
    (100, "SEVERE"),
)

# Severity-based floors: a CRITICAL cost finding pins the score into the
# SEVERE band even if the additive weights would have under-reported it.
# Without this, a single unbounded-loop CRITICAL could read MODERATE,
# which contradicts what the finding itself says.
_SEVERITY_FLOORS: dict[Severity, int] = {
    Severity.CRITICAL: 75,
    Severity.HIGH: 50,
    Severity.MEDIUM: 25,
}

# Honest framing for every finding description — keeps the free/paid boundary
# explicit without naming any paid product.
_ESTIMATE_NOTE = (
    "This is a structural estimate from graph topology only. "
    "Run-level cost measurement requires execution data."
)

# Per-finding weights (points added to the 0-100 Cost Risk Score).
_W_UNBOUNDED_LOOP = 25
_W_SELF_LOOP = 15
_W_FEEDBACK_LOOP_BASE = 8
_W_FEEDBACK_LOOP_PER_HOP = 2
_W_RETRY_PRONE = 6
_W_LONG_PATH_PER_HOP = 3
_W_FANOUT_PER_EXTRA = 3

# Topology thresholds for emitting non-cycle findings.
_LONG_PATH_THRESHOLD = 5  # hops
_FANOUT_THRESHOLD = 4  # out-degree


class CostRiskAttack(BaseAttack):
    """Static cost-waste risk analysis from graph topology alone.

    Findings:
    - CRITICAL — unbounded loop (cycle with no exit edge): infinite token burn risk.
    - CRITICAL — self-invocation loop: recursive token burn with no depth guard.
    - HIGH     — multi-agent feedback loop with exit: per-cycle cost scales with length.
    - HIGH     — fragile single-upstream dependency: retry-amplified token cost.
    - MEDIUM   — long critical path: every request pays the full chain cost.
    - MEDIUM   — high fan-out node: token spend multiplies per invocation.
    """

    name = "cost_risk"
    description = (
        "Static, relative estimate of which structural failures are most likely to "
        "waste tokens. Reads cycles, fragile dependencies, long chains, and high "
        "fan-out from the topology — no runtime data, no dollar amounts."
    )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, graph: Any) -> TestResult:
        g = graph.graph  # networkx.MultiDiGraph
        findings: list[Finding] = []

        if g.number_of_nodes() == 0:
            return TestResult(
                test_name=self.name,
                status=TestStatus.PASSED,
                findings=[],
                metrics={
                    "note": "empty graph",
                    "cost_risk_score": 0,
                    "cost_risk_verdict": "LOW",
                    "cost_risk_drivers": [],
                },
            )

        name_of: dict[str, str] = {nid: g.nodes[nid].get("name", nid) for nid in g.nodes()}

        # Collapse to a simple DiGraph for analysis (preserves direction, no dupes).
        simple: nx.DiGraph = nx.DiGraph()
        simple.add_nodes_from(g.nodes())
        for u, v in g.edges():
            simple.add_edge(u, v)

        score = 0
        drivers: list[str] = []

        role_ctx = getattr(graph, "role_context", None)
        # Only *declared* hubs suppress cycle / retry findings. Inferred-only
        # hubs are still subject to the full cost analysis since pure
        # structural inference isn't enough ground truth to silence findings.
        hub_ids: set[str] = (
            role_ctx.intentional_hubs if role_ctx is not None else set()
        )

        # 1) Self-loops and cycles (unbounded vs feedback).
        self_loop_findings, self_loop_score = self._scan_self_loops(g, name_of)
        findings.extend(self_loop_findings)
        score += self_loop_score
        if self_loop_findings:
            drivers.append(f"{len(self_loop_findings)} self-invocation loops")

        cycle_findings, cycle_score, unbounded_count, feedback_count = self._scan_cycles(
            simple, name_of, hub_ids=hub_ids
        )
        findings.extend(cycle_findings)
        score += cycle_score
        if unbounded_count:
            drivers.append(f"{unbounded_count} unbounded loop{'s' if unbounded_count != 1 else ''}")
        if feedback_count:
            drivers.append(f"{feedback_count} feedback loop{'s' if feedback_count != 1 else ''}")

        # 2) Fragile single-upstream (retry-prone) dependencies.
        retry_findings, retry_score = self._scan_retry_prone(simple, name_of, hub_ids=hub_ids)
        findings.extend(retry_findings)
        score += retry_score
        if retry_findings:
            drivers.append(
                f"{len(retry_findings)} retry-prone path{'s' if len(retry_findings) != 1 else ''}"
            )

        # 3) Long critical path.
        path_findings, path_score, longest_path_len = self._scan_long_critical_path(simple, name_of)
        findings.extend(path_findings)
        score += path_score
        if path_findings:
            drivers.append(f"critical path {longest_path_len} hops")

        # 4) High fan-out nodes.
        fanout_findings, fanout_score = self._scan_high_fanout(
            simple, name_of, hub_ids=hub_ids
        )
        findings.extend(fanout_findings)
        score += fanout_score
        if fanout_findings:
            drivers.append(
                f"{len(fanout_findings)} high fan-out node{'s' if len(fanout_findings) != 1 else ''}"
            )

        # Severity floor: the verdict band must never under-report the worst
        # finding. A single CRITICAL pins the score into SEVERE regardless of
        # how light the additive weights came out. Then clamp into [0, 100].
        score = max(score, _severity_floor(findings))
        score = max(0, min(100, score))
        verdict = _verdict_for(score)

        metrics: dict[str, Any] = {
            "cost_risk_score": int(score),
            "cost_risk_verdict": verdict,
            "cost_risk_drivers": drivers,
            "unbounded_loop_count": unbounded_count,
            "feedback_loop_count": feedback_count,
            "self_loop_count": len(self_loop_findings),
            "retry_prone_count": len(retry_findings),
            "long_critical_path_hops": longest_path_len,
            "high_fanout_count": len(fanout_findings),
        }

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,  # probe overrides based on finding severity
            findings=findings,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Self-loops (recursive self-invocation = unbounded token burn)
    # ------------------------------------------------------------------

    def _scan_self_loops(
        self,
        g: nx.MultiDiGraph,
        name_of: dict[str, str],
    ) -> tuple[list[Finding], int]:
        self_loop_ids = sorted(
            {u for u, v in g.edges() if u == v},
            key=lambda nid: name_of.get(nid, nid),
        )
        findings: list[Finding] = []
        for nid in self_loop_ids:
            nm = name_of.get(nid, nid)
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.CRITICAL,
                    title=f"Cost risk: CRITICAL — self-invocation loop on {nm}",
                    description=(
                        f"Agent '{nm}' calls itself. Without a visible recursion guard "
                        f"the agent can re-invoke itself indefinitely — every recursion "
                        f"spends tokens with no error to stop it. {_ESTIMATE_NOTE}"
                    ),
                    affected_agents=[nid],
                    evidence={
                        "agent_name": nm,
                        "cost_risk_label": "CRITICAL",
                        "factor": "self_loop",
                    },
                    remediation=(
                        f"Add an explicit max-iteration cap on '{nm}' to bound worst-case "
                        f"token spend, or replace the self-call with a bounded loop."
                    ),
                )
            )
        # Each self-loop adds points up to a cap (so a thousand self-loops doesn't pin to 100
        # before the other factors get a vote — but a single one is already SEVERE).
        score = min(len(self_loop_ids) * _W_SELF_LOOP, 45)
        return findings, score

    # ------------------------------------------------------------------
    # Cycles — unbounded vs feedback (with exit)
    # ------------------------------------------------------------------

    def _scan_cycles(
        self,
        simple: nx.DiGraph,
        name_of: dict[str, str],
        hub_ids: set[str] | None = None,
    ) -> tuple[list[Finding], int, int, int]:
        try:
            raw_cycles = [c for c in nx.simple_cycles(simple) if len(c) >= 2]
        except Exception as exc:  # defensive — networkx rarely raises here
            logger.debug("simple_cycles raised: %s", exc)
            raw_cycles = []

        findings: list[Finding] = []
        seen: set[frozenset[str]] = set()
        score = 0
        unbounded_count = 0
        feedback_count = 0
        hub_ids = hub_ids or set()

        # Sort cycles by sorted member-name tuple for deterministic order.
        def _cycle_key(c: list[str]) -> tuple[str, ...]:
            return tuple(sorted(name_of.get(nid, nid) for nid in c))

        for cycle in sorted(raw_cycles, key=_cycle_key):
            member_names = frozenset(name_of.get(nid, nid) for nid in cycle)
            if member_names in seen:
                continue
            seen.add(member_names)

            cycle_set = set(cycle)
            has_exit = any(
                succ not in cycle_set for nid in cycle for succ in simple.successors(nid)
            )

            # Worker ↔ Orchestrator return path is the normal request/response
            # pattern through an intentional hub. Only suppress when the cycle
            # has an exit edge (it's bounded by the hub's flow control), and at
            # least one member is a declared hub. A 2-node cycle whose only
            # exit is the hub still counts as a normal return path. A cycle
            # with NO exit (unbounded) is still flagged — even if the hub
            # routes through it, the topology lets it spin forever.
            cycle_hubs = cycle_set & hub_ids
            if has_exit and cycle_hubs:
                continue

            sorted_names = sorted(name_of.get(nid, nid) for nid in cycle)
            primary_name = sorted_names[0]
            primary_id = next(nid for nid in cycle if name_of.get(nid, nid) == primary_name)
            other_ids = [nid for nid in cycle if nid != primary_id]
            affected = [primary_id] + sorted(other_ids, key=lambda x: name_of.get(x, x))
            members_inline = ", ".join(sorted_names)
            length = len(cycle)

            if not has_exit:
                unbounded_count += 1
                score += _W_UNBOUNDED_LOOP
                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=Severity.CRITICAL,
                        title=(
                            f"Cost risk: CRITICAL — unbounded loop ({members_inline}) "
                            f"can re-invoke {length} agents indefinitely"
                        ),
                        description=(
                            f"A directed cycle of {length} agents ({members_inline}) has "
                            f"no exit edge. An unbounded loop can re-invoke these "
                            f"{length} agents indefinitely — every cycle spends tokens "
                            f"with no error to stop it. {_ESTIMATE_NOTE}"
                        ),
                        affected_agents=affected,
                        evidence={
                            "cycle_members": sorted_names,
                            "cycle_length": length,
                            "has_exit": False,
                            "cost_risk_label": "CRITICAL",
                            "factor": "unbounded_loop",
                        },
                        remediation=(
                            f"Add a max-iteration cap to bound worst-case token spend on "
                            f"the {members_inline} loop, or add a terminating edge that "
                            f"can route execution outside the cycle."
                        ),
                    )
                )
            else:
                feedback_count += 1
                weight = _W_FEEDBACK_LOOP_BASE + _W_FEEDBACK_LOOP_PER_HOP * length
                score += weight
                # 2-node feedback loops are also flagged by collusion (when
                # they bypass the orchestrator) and by trajectory ping-pong.
                # Demote to MEDIUM so a single architectural fact isn't worth
                # three independent HIGH/CRITICAL findings. Length 3+ cycles
                # describe distinct multi-agent feedback risk → keep HIGH.
                fb_severity = Severity.MEDIUM if length == 2 else Severity.HIGH
                fb_label = "MEDIUM" if length == 2 else "HIGH"
                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=fb_severity,
                        title=(
                            f"Cost risk: {fb_label} — feedback loop ({members_inline}) "
                            f"amplifies token spend per lap"
                        ),
                        description=(
                            f"A {length}-agent feedback loop ({members_inline}) has an "
                            f"exit path but each lap touches every member. Step count "
                            f"and token spend can amplify quickly when prompts drift or "
                            f"upstream outputs change. {_ESTIMATE_NOTE}"
                        ),
                        affected_agents=affected,
                        evidence={
                            "cycle_members": sorted_names,
                            "cycle_length": length,
                            "has_exit": True,
                            "cost_risk_label": fb_label,
                            "factor": "feedback_loop",
                        },
                        remediation=(
                            f"Add an explicit max-iteration cap on the {members_inline} "
                            f"loop so worst-case token spend is bounded, or split the "
                            f"loop into smaller stages with bounded handoffs."
                        ),
                    )
                )

        return findings, score, unbounded_count, feedback_count

    # ------------------------------------------------------------------
    # Retry-prone fragile single-upstream dependencies
    # ------------------------------------------------------------------

    def _scan_retry_prone(
        self,
        simple: nx.DiGraph,
        name_of: dict[str, str],
        hub_ids: set[str] | None = None,
    ) -> tuple[list[Finding], int]:
        """Flag agents with exactly one upstream and at least one downstream.

        A single-upstream node has no fallback path: if the upstream call fails,
        the downstream work was wasted and a retry re-spends the upstream's
        token cost too. This is a static retry-amplification signal.

        When the single upstream is the recognised intentional orchestrator,
        the dependency is by design — every spoke being fed by the hub is the
        whole point. We suppress those per-spoke findings (and emit a single
        aggregate finding describing the fan-out) so the report doesn't read
        as "every worker is fragile" for a perfectly normal hub topology.
        Non-orchestrator sole upstreams stay HIGH.
        """
        findings: list[Finding] = []
        candidates: list[tuple[str, str]] = []
        hub_ids = hub_ids or set()
        for nid in simple.nodes():
            preds = list(simple.predecessors(nid))
            succs = list(simple.successors(nid))
            if len(preds) == 1 and len(succs) >= 1:
                # Skip self-edges — already covered by self-loops.
                if preds[0] == nid:
                    continue
                candidates.append((nid, preds[0]))

        # Stable order by downstream then upstream name.
        candidates.sort(
            key=lambda pair: (name_of.get(pair[0], pair[0]), name_of.get(pair[1], pair[1]))
        )

        # Split candidates into hub-upstream (collapse) vs non-hub (emit each).
        hub_spokes: dict[str, list[tuple[str, str]]] = {}
        non_hub: list[tuple[str, str]] = []
        for nid, upstream_id in candidates:
            if upstream_id in hub_ids:
                hub_spokes.setdefault(upstream_id, []).append((nid, upstream_id))
            else:
                non_hub.append((nid, upstream_id))

        # Cycle dedup: in an N-node cycle every member has exactly one upstream
        # (its cycle predecessor) and at least one downstream, so each member
        # would emit its own "fragile dependency X → Y" finding. They describe
        # the same architectural fact — the cycle — and the unbounded/feedback
        # finding already names it. Collapse all per-edge findings whose
        # endpoints both live in the same SCC into one cycle-cluster finding.
        scc_groups: dict[frozenset[str], list[tuple[str, str]]] = {}
        non_cycle: list[tuple[str, str]] = []
        try:
            sccs = [scc for scc in nx.strongly_connected_components(simple) if len(scc) >= 2]
        except Exception:
            sccs = []
        scc_membership: dict[str, frozenset[str]] = {}
        for scc in sccs:
            frozen = frozenset(scc)
            for nid in scc:
                scc_membership[nid] = frozen
        for nid, upstream_id in non_hub:
            scc_a = scc_membership.get(nid)
            scc_b = scc_membership.get(upstream_id)
            if scc_a is not None and scc_a == scc_b:
                scc_groups.setdefault(scc_a, []).append((nid, upstream_id))
            else:
                non_cycle.append((nid, upstream_id))

        score = 0
        # Emit one collapsed finding per SCC that owns >= 2 fragile edges.
        # SCCs with a single edge fall back to the per-edge path (it's not
        # a cycle in any meaningful sense).
        for scc, group in scc_groups.items():
            if len(group) < 2:
                non_cycle.extend(group)
                continue
            member_names = sorted(name_of.get(nid, nid) for nid in scc)
            edge_strs = sorted(
                f"{name_of.get(u, u)}→{name_of.get(d, d)}" for d, u in group
            )
            sample = ", ".join(edge_strs[:5])
            if len(edge_strs) > 5:
                sample += f", … (+{len(edge_strs) - 5} more)"
            affected: list[str] = []
            seen_aff: set[str] = set()
            for d, u in group:
                for aid in (u, d):
                    if aid not in seen_aff:
                        seen_aff.add(aid)
                        affected.append(aid)
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.HIGH,
                    title=(
                        f"Cost risk: HIGH — cycle ({', '.join(member_names)}) creates "
                        f"{len(group)} fragile-dependency edges with no fallback"
                    ),
                    description=(
                        f"Each agent in the cycle ({', '.join(member_names)}) has "
                        f"exactly one upstream — its cycle predecessor — and no "
                        f"alternative path. {len(group)} fragile-dependency edges "
                        f"({sample}) all share the same root cause: the cycle. A "
                        f"failure anywhere in the loop forces every other member "
                        f"to wait or retry, compounding token cost per lap. The "
                        f"unbounded/feedback loop finding for this cycle names the "
                        f"primary risk; this record captures the per-edge "
                        f"fragility. {_ESTIMATE_NOTE}"
                    ),
                    affected_agents=affected,
                    evidence={
                        "cycle_members": member_names,
                        "fragile_edges": edge_strs,
                        "edge_count": len(group),
                        "cost_risk_label": "MEDIUM-HIGH",
                        "factor": "retry_prone_cycle",
                    },
                    remediation=(
                        f"Break the cycle ({', '.join(member_names)}) by adding an "
                        f"exit edge or memoising each member's output so a retry "
                        f"doesn't re-spend the predecessor's tokens. Fixing the "
                        f"cycle topology closes all {len(group)} fragile edges at once."
                    ),
                )
            )
            score += _W_RETRY_PRONE  # Single weight contribution for the cluster.

        for nid, upstream_id in non_cycle:
            downstream_name = name_of.get(nid, nid)
            upstream_name = name_of.get(upstream_id, upstream_id)
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.HIGH,
                    title=(
                        f"Cost risk: MEDIUM-HIGH — fragile dependency "
                        f"{upstream_name} → {downstream_name} (no fallback)"
                    ),
                    description=(
                        f"Agent '{downstream_name}' has exactly one upstream "
                        f"('{upstream_name}') and no alternative path. If "
                        f"'{upstream_name}' fails or returns a malformed payload, "
                        f"every retry re-spends '{upstream_name}'s tokens in addition "
                        f"to '{downstream_name}'s — token cost compounds per retry. "
                        f"{_ESTIMATE_NOTE}"
                    ),
                    affected_agents=sorted([nid, upstream_id], key=lambda x: name_of.get(x, x)),
                    evidence={
                        "downstream": downstream_name,
                        "upstream": upstream_name,
                        "cost_risk_label": "MEDIUM-HIGH",
                        "factor": "retry_prone",
                    },
                    remediation=(
                        f"Add a fallback upstream for '{downstream_name}' or memoize "
                        f"'{upstream_name}'s output so retries don't repay its token "
                        f"cost on every attempt."
                    ),
                )
            )
            score += _W_RETRY_PRONE
        # Cap retry-prone contribution so a long chain doesn't pin the score to 100 alone.
        score = min(score, 30)

        # Emit one INFO finding per hub that owns >=2 spokes — captures the
        # "every worker has the hub as its sole upstream" fact without firing
        # a HIGH per spoke. We intentionally emit nothing for single-spoke
        # hubs since that's already covered by other findings.
        for hub_id, group in hub_spokes.items():
            if len(group) < 2:
                continue
            hub_name = name_of.get(hub_id, hub_id)
            spoke_names = sorted(name_of.get(nid, nid) for nid, _ in group)
            spoke_label = ", ".join(spoke_names[:5])
            if len(spoke_names) > 5:
                spoke_label += f", … (+{len(spoke_names) - 5} more)"
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.INFO,
                    title=(
                        f"Cost risk: INFO — {len(spoke_names)} spokes share "
                        f"{hub_name} as sole upstream (intentional hub fan-out)"
                    ),
                    description=(
                        f"{len(spoke_names)} agents ({spoke_label}) each have "
                        f"'{hub_name}' as their only upstream. {hub_name} is the "
                        f"recognised intentional hub for this swarm, so this is "
                        f"the design — not a fragile dependency on a random "
                        f"single node. Retry-amplification risk still applies if "
                        f"'{hub_name}' fails; consider memoising its outputs so "
                        f"a retry doesn't re-spend its tokens. {_ESTIMATE_NOTE}"
                    ),
                    affected_agents=[hub_id] + [nid for nid, _ in group],
                    evidence={
                        "hub": hub_name,
                        "spokes": spoke_names,
                        "spoke_count": len(spoke_names),
                        "cost_risk_label": "INFO",
                        "factor": "intentional_hub_fanout",
                    },
                    remediation=(
                        f"Hub-and-spoke around '{hub_name}' is intentional. "
                        f"To bound retry cost, memoise '{hub_name}' outputs or "
                        f"add a hot standby so a single failure doesn't cascade."
                    ),
                )
            )

        return findings, score

    # ------------------------------------------------------------------
    # Long critical path
    # ------------------------------------------------------------------

    def _scan_long_critical_path(
        self,
        simple: nx.DiGraph,
        name_of: dict[str, str],
    ) -> tuple[list[Finding], int, int]:
        try:
            longest = nx.dag_longest_path(simple)
        except nx.NetworkXUnfeasible:
            # Graph has cycles — fall back to longest shortest path between any pair.
            longest = []
            try:
                lengths = dict(nx.all_pairs_shortest_path_length(simple))
                best: tuple[int, list[str]] = (0, [])
                for src, dsts in lengths.items():
                    for dst, length in dsts.items():
                        if length > best[0]:
                            best = (length, nx.shortest_path(simple, src, dst))
                longest = best[1]
            except Exception:
                longest = []

        hops = max(0, len(longest) - 1)
        if hops <= _LONG_PATH_THRESHOLD:
            return [], 0, hops

        path_names = [name_of.get(nid, nid) for nid in longest]
        excess = hops - _LONG_PATH_THRESHOLD
        score = min(excess * _W_LONG_PATH_PER_HOP, 20)
        primary_id = longest[0]
        path_str = " → ".join(path_names)
        finding = Finding(
            test_name=self.name,
            severity=Severity.MEDIUM,
            title=(
                f"Cost risk: MEDIUM — long critical path ({hops} hops) "
                f"pays the full chain cost on every request"
            ),
            description=(
                f"The longest path through the graph spans {hops} hops "
                f"({path_str}). Every request that follows this chain pays the "
                f"token cost of every hop, and a failure deep in the chain re-runs "
                f"every upstream agent's work. {_ESTIMATE_NOTE}"
            ),
            affected_agents=longest,
            evidence={
                "path": path_names,
                "hops": hops,
                "threshold": _LONG_PATH_THRESHOLD,
                "cost_risk_label": "MEDIUM",
                "factor": "long_critical_path",
            },
            remediation=(
                "Shorten the critical path by collapsing intermediate steps, "
                "adding result caching mid-chain, or parallelising independent "
                "stages so a deep failure doesn't re-spend upstream tokens."
            ),
        )
        _ = primary_id  # primary preserved via affected_agents[0]
        return [finding], score, hops

    # ------------------------------------------------------------------
    # High fan-out nodes
    # ------------------------------------------------------------------

    def _scan_high_fanout(
        self,
        simple: nx.DiGraph,
        name_of: dict[str, str],
        hub_ids: set[str] | None = None,
    ) -> tuple[list[Finding], int]:
        findings: list[Finding] = []
        score = 0
        hub_ids = hub_ids or set()
        # Sort by name for deterministic finding order.
        nodes_sorted = sorted(simple.nodes(), key=lambda nid: name_of.get(nid, nid))
        for nid in nodes_sorted:
            out_deg = simple.out_degree(nid)
            if out_deg <= _FANOUT_THRESHOLD:
                continue
            nm = name_of.get(nid, nid)
            downstream = sorted(
                (name_of.get(d, d) for d in simple.successors(nid)),
            )
            # High fan-out IS the design for an intentional hub. Demote to
            # INFO so the score isn't penalised for the architecture the
            # user declared, but still surface it.
            is_hub = nid in hub_ids
            severity = Severity.INFO if is_hub else Severity.MEDIUM
            title_label = "INFO" if is_hub else "MEDIUM"
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=severity,
                    title=(
                        f"Cost risk: {title_label} — high fan-out at {nm} "
                        f"(calls {out_deg} downstream agents per invocation)"
                    ),
                    description=(
                        f"Agent '{nm}' calls {out_deg} downstream agents "
                        f"({', '.join(downstream)}). Every invocation of '{nm}' "
                        f"multiplies token spend across the fan-out — small "
                        f"changes to '{nm}'s call pattern can balloon the per-request "
                        f"token budget. {_ESTIMATE_NOTE}"
                    ),
                    affected_agents=[nid],
                    evidence={
                        "agent_name": nm,
                        "out_degree": out_deg,
                        "downstream": downstream,
                        "cost_risk_label": title_label,
                        "factor": "high_fanout",
                        "is_intentional_hub": is_hub,
                    },
                    remediation=(
                        f"Gate downstream calls behind a router or filter so '{nm}' "
                        f"only invokes the agents it actually needs; consider "
                        f"batching identical downstream calls to share token cost."
                    ),
                )
            )
            excess = out_deg - _FANOUT_THRESHOLD
            score += excess * _W_FANOUT_PER_EXTRA
        score = min(score, 20)
        return findings, score


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _verdict_for(score: int) -> str:
    """Map a 0-100 cost-risk score to LOW / MODERATE / HIGH / SEVERE.

    Bands: 0–24 LOW, 25–49 MODERATE, 50–74 HIGH, 75–100 SEVERE.
    """
    for ceiling, label in _VERDICT_BANDS:
        if score <= ceiling:
            return label
    return _VERDICT_BANDS[-1][1]


def _severity_floor(findings: list[Finding]) -> int:
    """Minimum score implied by the highest-severity finding present.

    Walks ``_SEVERITY_FLOORS`` highest-first and returns the floor for the
    most severe finding. Returns 0 when no qualifying finding exists.
    """
    if not findings:
        return 0
    severities = {f.severity for f in findings}
    for sev, floor in _SEVERITY_FLOORS.items():
        if sev in severities:
            return floor
    return 0
