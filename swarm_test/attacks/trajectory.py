"""Trajectory Analysis Attack — detect loops, cycles, and runaway-path risks.

Static graph analysis only — no LLM calls. Flags structural risks that lead to
runaway step counts, retry storms, and token explosions even when individual
agent prompts look correct.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from swarm_test.attacks.base import BaseAttack
from swarm_test.core.models import Finding, Severity, TestResult, TestStatus

logger = logging.getLogger(__name__)


class TrajectoryAttack(BaseAttack):
    """Detect loops, cycles, repeated calls, and deep cyclic paths.

    Findings:
    - HIGH    — self-invocation loop (agent edge to itself)
    - CRITICAL — directed cycle with no exit edge to the rest of the graph
    - MEDIUM  — 2-node ping-pong cycle that does have an exit
    - HIGH    — 3+ node feedback loop that does have an exit
    - MEDIUM  — repeated parallel calls between the same agent pair
    - MEDIUM  — cycle length exceeds ``max_trajectory_depth`` (default 5)
    """

    name = "trajectory_analysis"
    description = (
        "Detects structural loop risks — cycles, self-invocation, repeated calls, "
        "and deep cyclic paths that cause runaway step counts or token explosions."
    )

    DEFAULT_MAX_DEPTH = 5

    def __init__(self, max_trajectory_depth: int | None = None) -> None:
        self.max_trajectory_depth = (
            int(max_trajectory_depth)
            if max_trajectory_depth is not None
            else self.DEFAULT_MAX_DEPTH
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, graph: Any) -> TestResult:
        findings: list[Finding] = []
        g = graph.graph  # networkx.MultiDiGraph

        if g.number_of_nodes() == 0:
            return TestResult(
                test_name=self.name,
                status=TestStatus.PASSED,
                findings=[],
                metrics={"note": "empty graph"},
            )

        name_of: dict[str, str] = {nid: g.nodes[nid].get("name", nid) for nid in g.nodes()}

        role_ctx = getattr(graph, "role_context", None)
        # Only *declared* hubs trigger cycle/duplicate-edge suppression.
        # Inferred-only hubs leave cycles intact so structural inference
        # alone cannot silence real loop signals.
        hub_ids: set[str] = role_ctx.intentional_hubs if role_ctx is not None else set()

        self_loop_findings, self_loop_ids = self._scan_self_loops(g, name_of)
        findings.extend(self_loop_findings)

        cycle_findings, seen_cycle_count = self._scan_cycles(
            g, name_of, self_loop_ids, hub_ids=hub_ids
        )
        findings.extend(cycle_findings)

        dup_findings, dup_pair_count = self._scan_duplicate_edges(g, name_of, hub_ids=hub_ids)
        findings.extend(dup_findings)

        metrics = {
            "self_loop_count": len(self_loop_ids),
            "cycle_count": seen_cycle_count,
            "duplicate_edge_pair_count": dup_pair_count,
            "max_trajectory_depth": self.max_trajectory_depth,
        }

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,  # probe overrides based on finding severity
            findings=findings,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Self-loops
    # ------------------------------------------------------------------

    def _scan_self_loops(
        self,
        g: nx.MultiDiGraph,
        name_of: dict[str, str],
    ) -> tuple[list[Finding], set[str]]:
        self_loop_ids = {u for u, v in g.edges() if u == v}
        ordered = sorted(self_loop_ids, key=lambda nid: name_of.get(nid, nid))
        findings: list[Finding] = []
        for nid in ordered:
            nm = name_of.get(nid, nid)
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.HIGH,
                    title=f"Self-invocation loop on agent {nm}",
                    description=(
                        f"Agent '{nm}' has an edge to itself — recursive self-invocation "
                        f"with no depth guard visible from the interaction topology."
                    ),
                    affected_agents=[nid],
                    evidence={"agent_name": nm, "loop_type": "self"},
                    remediation=(
                        f"Add a max-iteration guard or explicit recursion depth limit "
                        f"on '{nm}'. If the self-invocation is intentional, document "
                        f"the bounded exit condition so reviewers can verify it."
                    ),
                )
            )
        return findings, self_loop_ids

    # ------------------------------------------------------------------
    # Cycles
    # ------------------------------------------------------------------

    def _scan_cycles(
        self,
        g: nx.MultiDiGraph,
        name_of: dict[str, str],
        self_loop_ids: set[str],
        hub_ids: set[str] | None = None,
    ) -> tuple[list[Finding], int]:
        # Collapse to a simple DiGraph and drop self-loops (already reported).
        simple: nx.DiGraph = nx.DiGraph()
        simple.add_nodes_from(g.nodes())
        for u, v in g.edges():
            if u != v:
                simple.add_edge(u, v)

        try:
            raw_cycles = [c for c in nx.simple_cycles(simple) if len(c) >= 2]
        except Exception as exc:  # defensive — networkx rarely raises here
            logger.debug("simple_cycles raised: %s", exc)
            raw_cycles = []

        hub_ids = hub_ids or set()
        findings: list[Finding] = []
        # Dedupe cycles by frozenset of member NAMES so cycle orientation /
        # starting node doesn't produce duplicate findings.
        seen: set[frozenset[str]] = set()
        for cycle in raw_cycles:
            member_names = frozenset(name_of.get(nid, nid) for nid in cycle)
            if member_names in seen:
                continue
            seen.add(member_names)

            cycle_set = set(cycle)
            has_exit = any(
                succ not in cycle_set for nid in cycle for succ in simple.successors(nid)
            )

            # A bounded cycle that passes through the intentional hub is a
            # normal request/response chain — the hub's flow control bounds
            # it. Suppress these. Unbounded cycles (no exit edge) are still
            # flagged even when they include the hub.
            if has_exit and (cycle_set & hub_ids):
                continue

            sorted_names = sorted(name_of.get(nid, nid) for nid in cycle)
            primary_name = sorted_names[0]
            primary_id = next(nid for nid in cycle if name_of.get(nid, nid) == primary_name)
            other_ids = [nid for nid in cycle if nid != primary_id]
            affected = [primary_id] + sorted(other_ids, key=lambda x: name_of.get(x, x))

            members_label = " → ".join(sorted_names + [primary_name])
            members_inline = ", ".join(sorted_names)
            length = len(cycle)

            if not has_exit:
                title = (
                    f"Unbounded loop risk: agents {members_label} can loop " f"with no exit path"
                )
                severity = Severity.CRITICAL
                description = (
                    f"A directed cycle of {length} agents ({members_inline}) has "
                    f"no out-edge to any agent outside the cycle. Once execution "
                    f"enters the cycle there is no topological exit path."
                )
                remediation = (
                    f"Add an explicit exit edge (e.g., a terminating validator or "
                    f"max-iteration counter) on the {members_label} loop, or convert "
                    f"one edge into a conditional that can route outside the cycle."
                )
            elif length == 2:
                title = f"Ping-pong risk between {sorted_names[0]} and {sorted_names[1]}"
                # When the graph has an intentional hub, this 2-node bypass
                # cycle is also reported by collusion (orchestrator-bypass)
                # and by cost_risk (feedback loop). Demote to INFO so the
                # report doesn't triple-count the same fact. When there's no
                # hub at all the user has nothing else flagging this cycle,
                # so keep it as MEDIUM.
                graph_has_hub = bool(hub_ids)
                severity = Severity.INFO if graph_has_hub else Severity.MEDIUM
                description = (
                    f"Agents '{sorted_names[0]}' and '{sorted_names[1]}' send work "
                    f"back and forth. An exit edge exists, but a small prompt change "
                    f"can keep them bouncing for many turns before exiting."
                    + (
                        " The cost and collusion attacks also report this cycle — " "start there."
                        if graph_has_hub
                        else ""
                    )
                )
                remediation = (
                    f"Add a turn counter or explicit handoff condition on the "
                    f"{sorted_names[0]} ↔ {sorted_names[1]} edge so the loop is "
                    f"provably bounded."
                )
            else:
                title = (
                    f"Multi-agent feedback loop {members_label} ({length} agents) — "
                    f"step count can explode on a prompt change"
                )
                severity = Severity.HIGH
                description = (
                    f"A {length}-agent feedback loop ({members_inline}) has an exit "
                    f"path but multiple internal hops. Token and step counts can "
                    f"amplify quickly under prompt drift or upstream output changes."
                )
                remediation = (
                    f"Add a max-iteration guard or explicit exit condition on the "
                    f"{members_label} loop. Consider replacing the loop with a single "
                    f"orchestrated round-trip."
                )

            findings.append(
                Finding(
                    test_name=self.name,
                    severity=severity,
                    title=title,
                    description=description,
                    affected_agents=affected,
                    evidence={
                        "cycle_members": sorted_names,
                        "cycle_length": length,
                        "has_exit": has_exit,
                    },
                    remediation=remediation,
                )
            )

            # Deep cyclic path — additional MEDIUM when the cycle is long.
            if length > self.max_trajectory_depth:
                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=Severity.MEDIUM,
                        title=(
                            f"Deep cyclic path (length {length}) through "
                            f"{members_label} — token/step explosion risk"
                        ),
                        description=(
                            f"A cycle of length {length} passes through "
                            f"'{primary_name}' (threshold = "
                            f"{self.max_trajectory_depth}). Long cycles amplify "
                            f"per-iteration cost: each lap touches every member."
                        ),
                        affected_agents=affected,
                        evidence={
                            "cycle_members": sorted_names,
                            "cycle_length": length,
                            "max_trajectory_depth": self.max_trajectory_depth,
                        },
                        remediation=(
                            f"Shorten the {primary_name} loop, raise "
                            f"max_trajectory_depth if length {length} is intentional, "
                            f"or split the loop into smaller verifiable stages."
                        ),
                    )
                )

        # Filter out cycles whose only members are self-loop nodes already
        # reported — defensive: simple_cycles on the simple DiGraph never sees
        # self-loops since we drop them, so this is just future-proofing.
        if self_loop_ids:
            findings = [f for f in findings if not set(f.affected_agents).issubset(self_loop_ids)]

        return findings, len(seen)

    # ------------------------------------------------------------------
    # Duplicate / repeated edges
    # ------------------------------------------------------------------

    def _scan_duplicate_edges(
        self,
        g: nx.MultiDiGraph,
        name_of: dict[str, str],
        hub_ids: set[str] | None = None,
    ) -> tuple[list[Finding], int]:
        hub_ids = hub_ids or set()
        pair_counts: dict[tuple[str, str], int] = {}
        for u, v in g.edges():
            if u == v:
                continue  # self-loops handled separately
            # Duplicate edges between a worker and the intentional hub are
            # part of the normal request/response loop — the spoke might
            # call the hub several times for distinct subtasks. Skip pairs
            # where the hub is on either end.
            if u in hub_ids or v in hub_ids:
                continue
            pair_counts[(u, v)] = pair_counts.get((u, v), 0) + 1

        ordered_pairs = sorted(
            ((pair, count) for pair, count in pair_counts.items() if count >= 2),
            key=lambda kv: (name_of.get(kv[0][0], kv[0][0]), name_of.get(kv[0][1], kv[0][1])),
        )

        findings: list[Finding] = []
        for (u, v), count in ordered_pairs:
            src_name = name_of.get(u, u)
            dst_name = name_of.get(v, v)
            # Primary agent = lexically smaller of the two names — keeps
            # affected_agents[0] stable regardless of edge direction.
            if src_name <= dst_name:
                primary_id, other_id = u, v
            else:
                primary_id, other_id = v, u
            findings.append(
                Finding(
                    test_name=self.name,
                    severity=Severity.MEDIUM,
                    title=(
                        f"Repeated calls {src_name}→{dst_name} ({count} times) — "
                        f"possible redundant invocation / retry storm"
                    ),
                    description=(
                        f"The interaction {src_name} → {dst_name} appears {count} "
                        f"times in the recorded events. This may indicate retry "
                        f"storms, redundant invocations, or missing memoization."
                    ),
                    affected_agents=[primary_id, other_id],
                    evidence={
                        "source": src_name,
                        "target": dst_name,
                        "call_count": count,
                    },
                    remediation=(
                        f"Check whether {src_name}→{dst_name} should be memoized, "
                        f"batched, or rate-limited. If retries are intentional, "
                        f"ensure exponential backoff and a max-attempt cap are in place."
                    ),
                )
            )

        return findings, len(ordered_pairs)
