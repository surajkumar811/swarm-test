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

# Score thresholds for the 0-100 Cost Risk Score.
_VERDICT_BANDS: tuple[tuple[int, str], ...] = (
    (20, "LOW"),
    (50, "MODERATE"),
    (80, "HIGH"),
    (100, "SEVERE"),
)

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
        simple = nx.DiGraph()
        simple.add_nodes_from(g.nodes())
        for u, v in g.edges():
            simple.add_edge(u, v)

        score = 0
        drivers: list[str] = []

        # 1) Self-loops and cycles (unbounded vs feedback).
        self_loop_findings, self_loop_score = self._scan_self_loops(g, name_of)
        findings.extend(self_loop_findings)
        score += self_loop_score
        if self_loop_findings:
            drivers.append(f"{len(self_loop_findings)} self-invocation loops")

        cycle_findings, cycle_score, unbounded_count, feedback_count = self._scan_cycles(
            simple, name_of
        )
        findings.extend(cycle_findings)
        score += cycle_score
        if unbounded_count:
            drivers.append(f"{unbounded_count} unbounded loop{'s' if unbounded_count != 1 else ''}")
        if feedback_count:
            drivers.append(f"{feedback_count} feedback loop{'s' if feedback_count != 1 else ''}")

        # 2) Fragile single-upstream (retry-prone) dependencies.
        retry_findings, retry_score = self._scan_retry_prone(simple, name_of)
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
        fanout_findings, fanout_score = self._scan_high_fanout(simple, name_of)
        findings.extend(fanout_findings)
        score += fanout_score
        if fanout_findings:
            drivers.append(
                f"{len(fanout_findings)} high fan-out node{'s' if len(fanout_findings) != 1 else ''}"
            )

        # Clamp + verdict.
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
                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=Severity.HIGH,
                        title=(
                            f"Cost risk: HIGH — feedback loop ({members_inline}) "
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
                            "cost_risk_label": "HIGH",
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
    ) -> tuple[list[Finding], int]:
        """Flag agents with exactly one upstream and at least one downstream.

        A single-upstream node has no fallback path: if the upstream call fails,
        the downstream work was wasted and a retry re-spends the upstream's
        token cost too. This is a static retry-amplification signal.
        """
        findings: list[Finding] = []
        candidates: list[tuple[str, str]] = []
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

        score = 0
        for nid, upstream_id in candidates:
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
                best = (0, [])
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
    ) -> tuple[list[Finding], int]:
        findings: list[Finding] = []
        score = 0
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
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.MEDIUM,
                    title=(
                        f"Cost risk: MEDIUM — high fan-out at {nm} "
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
                        "cost_risk_label": "MEDIUM",
                        "factor": "high_fanout",
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
    """Map a 0-100 cost-risk score to LOW / MODERATE / HIGH / SEVERE."""
    for ceiling, label in _VERDICT_BANDS:
        if score <= ceiling:
            return label
    return _VERDICT_BANDS[-1][1]
