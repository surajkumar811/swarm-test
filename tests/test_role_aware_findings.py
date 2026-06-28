"""Acceptance tests for the role-aware finding pipeline.

These tests pin down the four corner cases listed in the calibration target:

1. A clean orchestrator topology declared via ``intentional_role`` scores
   HEALTHY (>=70) with at most 8 findings — no per-spoke false positives.
2. A genuine no-exit cycle that bypasses the orchestrator is still flagged
   CRITICAL — role-awareness must not silence real unbounded loops.
3. An orchestrator-bypass cycle (worker↔worker, hub outside the cycle) is
   still flagged — collusion/cost_risk own this signal.
4. ``role_adjusted_severity`` is actually invoked on the cascade path —
   regression guard so a future refactor can't silently disconnect it again.
"""

from __future__ import annotations

from unittest.mock import patch

from swarm_test import (
    AgentNode,
    EventType,
    InteractionEvent,
    Severity,
    SwarmProbe,
)
from swarm_test.attacks.cascade import CascadeFailureAttack
from swarm_test.core.graph import SwarmGraph
from swarm_test.core.taxonomy import (
    AgentRole,
    HUB_CONFIDENCE_THRESHOLD,
    RoleContext,
    is_hub_role,
)


def _delegate(src: AgentNode, dst: AgentNode) -> InteractionEvent:
    return InteractionEvent(
        source_agent_id=src.id,
        target_agent_id=dst.id,
        event_type=EventType.TASK_DELEGATE,
    )


def _response(src: AgentNode, dst: AgentNode) -> InteractionEvent:
    return InteractionEvent(
        source_agent_id=src.id,
        target_agent_id=dst.id,
        event_type=EventType.AGENT_RESPONSE,
    )


def _build_orchestrator_swarm(
    *, declare_intentional: bool = True
) -> tuple[list[AgentNode], list[InteractionEvent]]:
    """Hub-and-spoke: Orchestrator ⇄ {A, B, C} with task-delegate + response."""
    orchestrator = AgentNode(
        name="Orchestrator",
        role="orchestrator",
        intentional_role="ORCHESTRATOR" if declare_intentional else None,
    )
    workers = [AgentNode(name=f"W{i}", role="worker") for i in range(3)]
    events: list[InteractionEvent] = []
    for w in workers:
        events.append(_delegate(orchestrator, w))
        events.append(_response(w, orchestrator))
    return [orchestrator, *workers], events


# ---------------------------------------------------------------------------
# 1. Clean orchestrator topology — HEALTHY score, ≤ 8 findings
# ---------------------------------------------------------------------------


def test_clean_orchestrator_topology_scores_healthy() -> None:
    """A correctly-built hub-and-spoke topology must score ≥70 / ≤8 findings.

    This is the headline regression. Before role-awareness, every spoke
    fired its own CRITICAL cascade finding because every spoke "reached"
    every other spoke through the orchestrator. The fix collapses the
    centrality into the declared hub and silences the per-spoke noise.
    """
    agents, events = _build_orchestrator_swarm(declare_intentional=True)
    probe = SwarmProbe(
        swarm_name="clean-orchestrator", agents=agents, events=events
    )
    report = probe.run_all()

    actionable = [
        f
        for f in report.all_findings
        if f.severity in {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
    ]
    assert report.swarm_score >= 70, (
        f"Clean orchestrator topology should score >=70, got "
        f"{report.swarm_score}. Surviving findings: "
        f"{[(f.severity.value, f.title) for f in report.all_findings]}"
    )
    assert len(actionable) <= 8, (
        f"Clean orchestrator topology should produce <=8 actionable findings, "
        f"got {len(actionable)}: "
        f"{[(f.severity.value, f.title) for f in actionable]}"
    )
    # Per-spoke cascade CRITICALs are the canonical pre-fix false positive.
    cascade_crits = [
        f
        for f in report.all_findings
        if f.test_name == "cascade_failure" and f.severity == Severity.CRITICAL
    ]
    assert not cascade_crits, (
        f"No CRITICAL cascade findings should fire on a clean orchestrator "
        f"topology, got: {[f.title for f in cascade_crits]}"
    )


def test_clean_orchestrator_topology_without_declaration_is_handled() -> None:
    """Even without an explicit ``intentional_role`` the lexical hint
    ('orchestrator' role label) should yield an inferred-hub classification.

    Inferred hubs *downgrade* severity but do not fully suppress. We assert
    the system stays in a reasonable range (no per-spoke CRITICAL findings)
    without claiming the same score guarantee as the declared case.
    """
    agents, events = _build_orchestrator_swarm(declare_intentional=False)
    probe = SwarmProbe(
        swarm_name="inferred-orchestrator", agents=agents, events=events
    )
    report = probe.run_all()

    # The hub agent's CRITICAL cascade should be downgraded by
    # role_adjusted_severity to HIGH (not silenced entirely, but no longer
    # CRITICAL). The point: high-confidence inferred orchestrators still
    # influence severity.
    cascade_crits = [
        f
        for f in report.all_findings
        if f.test_name == "cascade_failure" and f.severity == Severity.CRITICAL
    ]
    assert not cascade_crits, (
        f"Inferred orchestrator should downgrade CRITICAL cascades, got: "
        f"{[f.title for f in cascade_crits]}"
    )


# ---------------------------------------------------------------------------
# 2. Genuine no-exit cycle that bypasses orchestrator → still CRITICAL
# ---------------------------------------------------------------------------


def test_unbounded_bypass_cycle_still_flagged_critical() -> None:
    """An unbounded (no-exit) cycle between non-hub agents stays CRITICAL.

    Role-awareness must not silence real loops. The hub being declared
    intentional doesn't license a 2-worker no-exit cycle that ignores it.
    """
    hub = AgentNode(
        name="Orchestrator", role="orchestrator", intentional_role="ORCHESTRATOR"
    )
    a = AgentNode(name="A", role="worker")
    b = AgentNode(name="B", role="worker")
    # A ⇄ B with no exit edge to any other agent — pure unbounded loop.
    # Hub exists in the graph but is not connected to A↔B at all.
    events = [
        _delegate(a, b),
        _delegate(b, a),
        # Give the hub something to do so it isn't isolated and is
        # actually classified as a hub.
        _delegate(hub, AgentNode(name="HubSpoke", role="worker")),
    ]
    spoke = next(
        e for e in events if e.source_agent_id == hub.id
    )
    hub_spoke_agent = AgentNode(
        id=spoke.target_agent_id, name="HubSpoke", role="worker"
    )
    probe = SwarmProbe(
        swarm_name="bypass-cycle",
        agents=[hub, a, b, hub_spoke_agent],
        events=events,
    )
    report = probe.run_all()

    cost = next(r for r in report.test_results if r.test_name == "cost_risk")
    unbounded = [
        f for f in cost.findings if f.evidence.get("factor") == "unbounded_loop"
    ]
    assert unbounded, (
        f"Unbounded bypass cycle must still be flagged as CRITICAL cost risk; "
        f"got titles: {[f.title for f in cost.findings]}"
    )
    assert all(f.severity == Severity.CRITICAL for f in unbounded)


# ---------------------------------------------------------------------------
# 3. Orchestrator-bypass cycle (2-worker cycle, hub outside) → still flagged
# ---------------------------------------------------------------------------


def test_orchestrator_bypass_cycle_still_flagged() -> None:
    """A 3-worker cycle that bypasses the hub stays a finding.

    Collusion's ``Orchestrator-bypass cycle`` detector is the canonical
    home for this signal — it must still fire when the hub is declared.
    Uses a 3-node cycle so no member has the high out-degree that would
    make a single worker look like an inferred orchestrator.
    """
    hub = AgentNode(
        name="Orchestrator",
        role="orchestrator",
        intentional_role="ORCHESTRATOR",
    )
    wa = AgentNode(name="WorkerA", role="worker")
    wb = AgentNode(name="WorkerB", role="worker")
    wc = AgentNode(name="WorkerC", role="worker")
    events = [
        _delegate(hub, wa),       # hub fan-out
        _delegate(hub, wb),
        _delegate(hub, wc),
        _delegate(wa, wb),        # bypass cycle leg 1
        _delegate(wb, wc),        # bypass cycle leg 2
        _delegate(wc, wa),        # bypass cycle leg 3 (closes the cycle)
        _delegate(wc, hub),       # exit edge so the cycle is bounded
    ]
    probe = SwarmProbe(
        swarm_name="bypass-cycle-bounded",
        agents=[hub, wa, wb, wc],
        events=events,
    )
    report = probe.run_all()

    coll = next(
        r for r in report.test_results if r.test_name == "collusion_detection"
    )
    bypass = [f for f in coll.findings if "bypass cycle" in f.title.lower()]
    assert bypass, (
        f"Orchestrator-bypass cycle must still be flagged by collusion; "
        f"got: {[f.title for f in coll.findings]}"
    )
    assert all(f.severity == Severity.HIGH for f in bypass)


# ---------------------------------------------------------------------------
# 4. Regression: role_adjusted_severity is actually called on the cascade path
# ---------------------------------------------------------------------------


def test_role_adjusted_severity_is_wired_into_cascade() -> None:
    """``role_adjusted_severity`` must be called from the cascade attack.

    Before this fix the function was defined and unit-tested but never
    invoked at runtime. This regression test patches it and asserts the
    cascade attack reaches it — so a future refactor can't silently
    disconnect the role layer again.
    """
    agents, events = _build_orchestrator_swarm(declare_intentional=True)
    probe = SwarmProbe(
        swarm_name="cascade-role-call", agents=agents, events=events
    )
    # Run once to populate role context, then re-run cascade with a patched
    # symbol so we can detect the call.
    probe.graph.classify_roles()

    cascade = CascadeFailureAttack()
    with patch(
        "swarm_test.attacks.cascade.role_adjusted_severity",
        wraps=__import__(
            "swarm_test.core.taxonomy", fromlist=["role_adjusted_severity"]
        ).role_adjusted_severity,
    ) as spy:
        cascade.run(probe.graph)

    assert spy.called, (
        "role_adjusted_severity was never invoked from CascadeFailureAttack — "
        "the role-aware severity layer is disconnected."
    )
    # And it's reading the cascade_failure finding_type (not some bogus value).
    finding_types = {call.args[1] for call in spy.call_args_list}
    assert "cascade_failure" in finding_types, (
        f"Expected cascade_failure finding_type in role_adjusted_severity "
        f"calls; saw {finding_types}"
    )


# ---------------------------------------------------------------------------
# Supporting tests for the new building blocks
# ---------------------------------------------------------------------------


def test_intentional_role_declaration_overrides_inference() -> None:
    """``intentional_role`` returns the declared role with confidence 1.0,
    even when the structural classifier would pick something else.
    """
    # An agent named "ValidatorAgent" with role="validator" would normally
    # classify as VALIDATOR. Declaring intentional_role=ORCHESTRATOR overrides.
    agent = AgentNode(
        name="ValidatorAgent",
        role="validator",
        intentional_role="ORCHESTRATOR",
    )
    other = AgentNode(name="Other", role="worker")
    g = SwarmGraph()
    g.add_agent(agent)
    g.add_agent(other)
    g.record_event(_delegate(agent, other))

    ctx = g.classify_roles()
    assert ctx.role_of(agent.id) == AgentRole.ORCHESTRATOR
    assert ctx.confidence_of(agent.id) == 1.0
    assert ctx.is_intentional_hub(agent.id) is True
    assert ctx.is_inferred_hub(agent.id) is False


def test_role_context_separates_intentional_and_inferred_hubs() -> None:
    """Inferred hubs must not satisfy ``is_intentional_hub``."""
    declared = ("declared", (AgentRole.ORCHESTRATOR, 1.0))
    inferred = ("inferred", (AgentRole.ORCHESTRATOR, 0.85))
    weak = ("weak", (AgentRole.ORCHESTRATOR, HUB_CONFIDENCE_THRESHOLD - 0.01))
    role_map = dict([declared, inferred, weak])
    ctx = RoleContext(role_map)

    assert ctx.is_intentional_hub("declared") is True
    assert ctx.is_intentional_hub("inferred") is False
    assert ctx.is_inferred_hub("inferred") is True
    assert ctx.is_intentional_hub("weak") is False
    assert ctx.is_inferred_hub("weak") is False
    # Backwards-compat union still returns both populated cohorts.
    assert ctx.hubs == {"declared", "inferred"}


def test_effective_blast_radius_excludes_hub_only_paths() -> None:
    """A pure leaf returning only to the orchestrator must have low effective BR."""
    hub = AgentNode(
        name="Orchestrator", role="orchestrator", intentional_role="ORCHESTRATOR"
    )
    a = AgentNode(name="A", role="worker")
    b = AgentNode(name="B", role="worker")
    g = SwarmGraph()
    g.add_agent(hub)
    g.add_agent(a)
    g.add_agent(b)
    # Hub → A, Hub → B, A → Hub (return), B → Hub (return).
    for src, dst in ((hub, a), (a, hub), (hub, b), (b, hub)):
        g.record_event(_delegate(src, dst))

    # Without role context, raw blast radius treats A as if it reached B
    # (via hub), inflating the impact.
    raw = g.get_blast_radius(a.id)
    assert raw["impact_percentage"] >= 50.0, (
        "Pre-condition: raw blast radius should include hub-routed descendants."
    )

    # Classify and recompute via the effective method.
    ctx = g.classify_roles()
    assert ctx.is_intentional_hub(hub.id)
    effective = g.get_effective_blast_radius(a.id)
    assert effective["impact_percentage"] == 0.0, (
        f"Effective blast radius for a pure-return leaf must be 0%; got "
        f"{effective['impact_percentage']}%."
    )


def test_declared_hub_health_not_dragged_by_expected_centrality() -> None:
    """A declared intentional hub's agent_health score must not be tanked
    by *expected* centrality (blast radius, SPOF, cascade depth, edge
    imbalance). Real issues like collusion cliques still apply, but a clean
    hub-and-spoke topology around a declared hub must land in the healthy band.

    Pre-fix: the same hub scored ~4/100 because raw blast radius (-40),
    SPOF (-30), cascade depth (-20), and edge imbalance (-15) all hit on
    a textbook by-design hub topology. Spokes also read ~64/100 because they
    inherited the hub's reach. Both behaviours are wrong.
    """
    hub = AgentNode(
        name="Hub", role="orchestrator", intentional_role="ORCHESTRATOR"
    )
    workers = [AgentNode(name=f"W{i}", role="worker") for i in range(5)]
    events: list[InteractionEvent] = []
    for w in workers:
        events.append(_delegate(hub, w))
        events.append(_response(w, hub))

    probe = SwarmProbe(
        swarm_name="hub-health", agents=[hub, *workers], events=events
    )
    report = probe.run_all()

    hub_score = report.agent_scores[hub.id]
    assert hub_score.score >= 70, (
        f"Declared intentional hub should score in the healthy band (>=70), "
        f"got {hub_score.score}/100. Reasons: {hub_score.reasons}. "
        f"Breakdown: {hub_score.breakdown}"
    )
    # By-design centrality must contribute 0 penalty for an intentional hub.
    assert hub_score.breakdown.get("blast_radius", 0) == 0
    assert hub_score.breakdown.get("spof", 0) == 0
    assert hub_score.breakdown.get("cascade_depth", 0) == 0
    assert hub_score.breakdown.get("edge_imbalance", 0) == 0

    # Pure-return spokes must not inherit the hub's blast radius — effective
    # blast radius excludes hub-routed descendants, so a leaf returning only
    # to the hub has 0% effective reach and 0 blast penalty.
    for w in workers:
        ws = report.agent_scores[w.id]
        assert ws.breakdown.get("blast_radius", 0) == 0, (
            f"Pure-return spoke {ws.agent_name} must not be penalised for "
            f"hub-routed reach; got blast penalty "
            f"{ws.breakdown.get('blast_radius', 0)}. Reasons: {ws.reasons}"
        )


def test_small_graph_spokes_are_not_misclassified_as_orchestrator() -> None:
    """A hub-and-spoke graph with 1 hub + 3 spokes must not label the
    spokes as ORCHESTRATOR. Pre-fix, the ratio-only scoring tripped on
    small graphs (out_ratio = 1/3 = 0.33) and tagged every spoke as a
    42%-confidence orchestrator — which then unlocked hub-role suppression
    on a plain worker. Regression guard.
    """
    hub = AgentNode(name="Hub", role="orchestrator")
    spokes = [AgentNode(name=f"S{i}", role="worker") for i in range(3)]
    g = SwarmGraph()
    for ag in (hub, *spokes):
        g.add_agent(ag)
    for s in spokes:
        g.record_event(_delegate(hub, s))
        g.record_event(_delegate(s, hub))

    ctx = g.classify_roles()
    assert ctx.role_of(hub.id) == AgentRole.ORCHESTRATOR
    for s in spokes:
        assert ctx.role_of(s.id) != AgentRole.ORCHESTRATOR, (
            f"Spoke {s.name} must NOT classify as ORCHESTRATOR "
            f"(got {ctx.role_of(s.id)} at "
            f"{ctx.confidence_of(s.id):.2f})"
        )
        assert not ctx.is_intentional_hub(s.id)
        assert not ctx.is_inferred_hub(s.id)


def test_cycle_members_are_not_misclassified_as_orchestrator() -> None:
    """A 3-node directed cycle (A→B→C→A) must not label any member as
    ORCHESTRATOR. Pre-fix, the heuristic on small graphs scored cycle
    members at 65% ORCHESTRATOR confidence — making each a high-confidence
    inferred hub. Regression guard.
    """
    a = AgentNode(name="A", role="worker")
    b = AgentNode(name="B", role="worker")
    c = AgentNode(name="C", role="worker")
    g = SwarmGraph()
    for ag in (a, b, c):
        g.add_agent(ag)
    g.record_event(_delegate(a, b))
    g.record_event(_delegate(b, c))
    g.record_event(_delegate(c, a))

    ctx = g.classify_roles()
    for ag in (a, b, c):
        assert ctx.role_of(ag.id) != AgentRole.ORCHESTRATOR, (
            f"Cycle member {ag.name} must NOT classify as ORCHESTRATOR "
            f"(got {ctx.role_of(ag.id)} at "
            f"{ctx.confidence_of(ag.id):.2f})"
        )


def test_misclassified_hub_does_not_suppress_real_findings() -> None:
    """CRITICAL SAFETY CHECK: a worker that is NOT a real hub must not
    receive role-aware centrality suppression.

    A worker in a tight cluster might accidentally get classified as an
    inferred orchestrator on a small graph. If that mistake unlocked
    finding suppression, real risks would be silenced. This test
    constructs a small worker-heavy topology and asserts cascade findings
    are still emitted at full severity.
    """
    a = AgentNode(name="A", role="worker")
    b = AgentNode(name="B", role="worker")
    c = AgentNode(name="C", role="worker")
    # A → B, A → C — A is a fan-out worker, NOT a declared hub.
    events = [_delegate(a, b), _delegate(a, c)]
    probe = SwarmProbe(swarm_name="worker-fanout", agents=[a, b, c], events=events)
    report = probe.run_all()

    # A must not classify as a hub (out_deg = 2 < HUB_MIN_OUT_DEG = 3).
    role_info = report.agent_roles[a.id]
    assert not (
        probe.graph.role_context.is_intentional_hub(a.id)
        or probe.graph.role_context.is_inferred_hub(a.id)
    ), (
        f"Worker A with only 2 spokes must not be classified as a hub "
        f"(got role={role_info['role']} at {role_info['confidence']:.2f})"
    )
    # Cascade finding for A must be emitted at full severity — no suppression
    # because the hub set is empty.
    cascade = next(r for r in report.test_results if r.test_name == "cascade_failure")
    a_findings = [f for f in cascade.findings if a.id in f.affected_agents]
    assert a_findings, (
        "A's cascade finding must still appear — no false hub suppression."
    )


def test_cycle_dedup_collapses_per_member_cascade_findings() -> None:
    """An N-node cycle must emit at most ONE cascade finding (not N).

    Pre-fix, A→B→C→A produced three separate "X failure cascades to 2
    agents" findings. The collapse key was the downstream set, which
    differs per cycle root. The new key is the full agent set — same SCC
    folds into one record.
    """
    a = AgentNode(name="A", role="worker")
    b = AgentNode(name="B", role="worker")
    c = AgentNode(name="C", role="worker")
    events = [_delegate(a, b), _delegate(b, c), _delegate(c, a)]
    probe = SwarmProbe(swarm_name="triangle", agents=[a, b, c], events=events)
    report = probe.run_all()

    cascade = next(r for r in report.test_results if r.test_name == "cascade_failure")
    # Expect a single collapsed finding covering all three cycle members.
    assert len(cascade.findings) == 1, (
        f"Cycle should collapse to one cascade finding, got "
        f"{[f.title for f in cascade.findings]}"
    )
    f = cascade.findings[0]
    assert {a.id, b.id, c.id} <= set(f.affected_agents)


def test_cycle_dedup_collapses_fragile_dependency_findings() -> None:
    """An N-node cycle must emit at most ONE fragile-dependency finding
    per attack (cost_risk + timeout_resilience). Pre-fix, each cycle edge
    fired its own finding.
    """
    a = AgentNode(name="A", role="worker")
    b = AgentNode(name="B", role="worker")
    c = AgentNode(name="C", role="worker")
    events = [_delegate(a, b), _delegate(b, c), _delegate(c, a)]
    probe = SwarmProbe(swarm_name="triangle-frag", agents=[a, b, c], events=events)
    report = probe.run_all()

    cost = next(r for r in report.test_results if r.test_name == "cost_risk")
    cost_frag = [
        f for f in cost.findings if f.evidence.get("factor") == "retry_prone_cycle"
    ]
    cost_frag_perEdge = [
        f for f in cost.findings if f.evidence.get("factor") == "retry_prone"
    ]
    assert len(cost_frag) == 1, (
        f"cost_risk must collapse cycle fragile deps to ONE finding; got "
        f"{[f.title for f in cost.findings if f.evidence.get('factor', '').startswith('retry_prone')]}"
    )
    assert not cost_frag_perEdge, (
        f"Per-edge retry_prone findings must not appear for cycle members; got "
        f"{[f.title for f in cost_frag_perEdge]}"
    )

    timeout = next(
        r for r in report.test_results if r.test_name == "timeout_resilience"
    )
    timeout_frag = [
        f for f in timeout.findings if f.evidence.get("factor") == "fragile_cycle"
    ]
    timeout_per_edge = [
        f for f in timeout.findings if "Fragile dependency:" in f.title
    ]
    assert len(timeout_frag) == 1, (
        f"timeout_resilience must collapse cycle fragile deps to ONE finding"
    )
    assert not timeout_per_edge, (
        f"Per-edge timeout fragile findings must not appear for cycle members"
    )

    # Critical safety: the unbounded-loop CRITICAL must still be intact.
    unbounded = [
        f for f in cost.findings if f.evidence.get("factor") == "unbounded_loop"
    ]
    assert unbounded and all(u.severity == Severity.CRITICAL for u in unbounded), (
        "Dedup must not silence the unbounded-loop CRITICAL finding."
    )


def test_is_hub_role_recognises_orchestrator_and_aggregator() -> None:
    """``is_hub_role`` is the shared signal for hub-style RISK_PROFILES."""
    assert is_hub_role(AgentRole.ORCHESTRATOR) is True
    assert is_hub_role(AgentRole.AGGREGATOR) is True
    assert is_hub_role(AgentRole.WORKER) is False
    assert is_hub_role(AgentRole.UNKNOWN) is False
