"""Tests for swarm_test.history.HistoryStore."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from swarm_test import Finding, Severity, SwarmReport, TestResult, TestStatus
from swarm_test.history import HistoryStore
from swarm_test.scoring.agent_health import AgentHealthScore


def _make_report(
    swarm_name: str = "demo",
    findings: list[Finding] | None = None,
    test_results: list[TestResult] | None = None,
) -> SwarmReport:
    """Build a minimal SwarmReport with the requested findings."""
    if findings is None:
        findings = []
    if test_results is None:
        # Bundle every finding into a single synthetic test result so
        # SwarmReport.all_findings picks them up.
        test_results = [
            TestResult(
                test_name="synthetic",
                status=TestStatus.FAILED if findings else TestStatus.PASSED,
                findings=list(findings),
            )
        ]
    return SwarmReport(
        swarm_name=swarm_name,
        framework="static",
        agent_count=2,
        edge_count=1,
        test_results=test_results,
    )


def _finding(
    severity: Severity = Severity.HIGH,
    title: str = "Default finding",
    test_name: str = "synthetic",
    affected: list[str] | None = None,
) -> Finding:
    return Finding(
        test_name=test_name,
        severity=severity,
        title=title,
        description=f"{title} description",
        affected_agents=affected or ["agent-a"],
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_creates_file(tmp_path: Path) -> None:
    """save() writes a JSON file to the history directory."""
    store = HistoryStore(tmp_path / "history")
    report = _make_report(swarm_name="alpha", findings=[_finding()])

    path = store.save(report)

    saved = Path(path)
    assert saved.exists(), "save() should create the snapshot file"
    assert saved.parent == tmp_path / "history"
    assert saved.suffix == ".json"
    assert "alpha" in saved.name


def test_load_recent_returns_newest_first(tmp_path: Path) -> None:
    """load_recent() must return entries sorted newest → oldest."""
    store = HistoryStore(tmp_path / "history")
    store.save(_make_report(swarm_name="alpha"))
    # Different filenames have second resolution; force a gap.
    time.sleep(1.05)
    store.save(_make_report(swarm_name="alpha"))
    time.sleep(1.05)
    store.save(_make_report(swarm_name="alpha"))

    entries = store.load_recent(n=10)
    timestamps = [e["timestamp"] for e in entries]
    assert len(entries) == 3
    assert timestamps == sorted(timestamps, reverse=True), "Entries should be newest-first"


def test_load_recent_respects_n(tmp_path: Path) -> None:
    """load_recent(n=2) must cap the result count."""
    store = HistoryStore(tmp_path / "history")
    for _ in range(5):
        store.save(_make_report(swarm_name="alpha"))
        time.sleep(1.05)

    entries = store.load_recent(n=2)
    assert len(entries) == 2


def test_get_previous_none_when_empty(tmp_path: Path) -> None:
    """get_previous() returns None when there is no prior history."""
    store = HistoryStore(tmp_path / "history")
    assert store.get_previous() is None
    assert store.get_previous(swarm_name="anything") is None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_compare_first_run(tmp_path: Path) -> None:
    """compare_to_previous() flags first_run when history is empty."""
    store = HistoryStore(tmp_path / "history")
    report = _make_report(swarm_name="alpha", findings=[_finding()])

    comparison = store.compare_to_previous(report)
    assert comparison == {"first_run": True}


def test_compare_improving(tmp_path: Path) -> None:
    """A higher current swarm_score than the previous → improving trend."""
    store = HistoryStore(tmp_path / "history")
    # First run: lots of findings (lower score).
    noisy = _make_report(
        swarm_name="alpha",
        findings=[_finding(Severity.CRITICAL), _finding(Severity.HIGH, title="HighA")],
    )
    store.save(noisy)
    time.sleep(1.05)

    # Second run: zero findings → highest possible score.
    quiet = _make_report(swarm_name="alpha")
    comparison = store.compare_to_previous(quiet)

    assert comparison["first_run"] is False
    assert comparison["trend"] == "improving"
    assert comparison["swarm_score_delta"] > 0


def test_compare_declining(tmp_path: Path) -> None:
    """A lower current swarm_score than previous → declining trend."""
    store = HistoryStore(tmp_path / "history")
    # First run: clean.
    store.save(_make_report(swarm_name="alpha"))
    time.sleep(1.05)

    # Second run: lots of critical findings.
    noisy = _make_report(
        swarm_name="alpha",
        findings=[
            _finding(Severity.CRITICAL, title="C1"),
            _finding(Severity.CRITICAL, title="C2"),
            _finding(Severity.HIGH, title="H1"),
        ],
    )
    comparison = store.compare_to_previous(noisy)

    assert comparison["first_run"] is False
    assert comparison["trend"] == "declining"
    assert comparison["swarm_score_delta"] < 0


def test_compare_new_and_resolved_findings(tmp_path: Path) -> None:
    """Finding-ID diffing should surface both new and resolved findings."""
    store = HistoryStore(tmp_path / "history")
    # Previous: A + B.
    previous = _make_report(
        swarm_name="alpha",
        findings=[
            _finding(title="Shared", affected=["agent-shared"]),
            _finding(title="OldOnly", affected=["agent-old"]),
        ],
    )
    store.save(previous)
    time.sleep(1.05)

    # Current: Shared + NewOnly (so OldOnly is resolved, NewOnly is new).
    current = _make_report(
        swarm_name="alpha",
        findings=[
            _finding(title="Shared", affected=["agent-shared"]),
            _finding(title="NewOnly", affected=["agent-new"]),
        ],
    )
    comparison = store.compare_to_previous(current)

    new_titles = {f["title"] for f in comparison["new_findings"]}
    resolved_titles = {f["title"] for f in comparison["resolved_findings"]}
    assert new_titles == {"NewOnly"}
    assert resolved_titles == {"OldOnly"}


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


def test_prune_keeps_most_recent(tmp_path: Path) -> None:
    """prune(keep=2) keeps the two newest entries per swarm, deletes the rest."""
    store = HistoryStore(tmp_path / "history")
    saved_paths: list[str] = []
    for _ in range(5):
        saved_paths.append(store.save(_make_report(swarm_name="alpha")))
        time.sleep(1.05)

    deleted = store.prune(keep=2)
    assert deleted == 3

    entries = store.load_recent(n=10, swarm_name="alpha")
    assert len(entries) == 2

    # The two surviving entries should be the most recent saves (last two paths).
    surviving = {Path(p).name for p in saved_paths[-2:]}
    actual = {f.name for f in (tmp_path / "history").glob("*.json")}
    assert surviving == actual


# ---------------------------------------------------------------------------
# Stable finding identity — regression test for the UUID-churn bug.
# ---------------------------------------------------------------------------


def _report_with_named_agent(
    swarm_name: str,
    agent_name: str,
    findings: list[Finding],
) -> SwarmReport:
    """Build a report where the finding's affected_agents are a fresh UUID,
    but a matching agent_name is recorded in agent_scores. Mirrors what
    SwarmProbe.run_all() produces — the agent IDs regenerate per run but
    the names stay stable."""
    agent_id = str(uuid.uuid4())
    rewired: list[Finding] = []
    for f in findings:
        # Replace the placeholder agent id with this run's fresh UUID.
        rewired.append(
            Finding(
                test_name=f.test_name,
                severity=f.severity,
                title=f.title,
                description=f.description,
                affected_agents=[agent_id],
            )
        )
    report = SwarmReport(
        swarm_name=swarm_name,
        framework="static",
        agent_count=1,
        edge_count=0,
        test_results=[
            TestResult(test_name="synthetic", status=TestStatus.FAILED, findings=rewired)
        ],
        agent_scores={
            agent_id: AgentHealthScore(
                agent_id=agent_id,
                agent_name=agent_name,
                role="worker",
                score=80,
            )
        },
    )
    return report


def test_identical_runs_no_diff(tmp_path: Path) -> None:
    """Two identical topologies (same agent NAMES, fresh UUIDs) → 0 new / 0 resolved.

    Regression test for the v0.3.5 bug where finding identity hashed the
    per-run UUIDs, so every run looked like a complete churn.
    """
    store = HistoryStore(tmp_path / "history")
    findings = [
        _finding(Severity.CRITICAL, title="SPOF detected"),
        _finding(Severity.HIGH, title="High blast radius"),
    ]
    first = _report_with_named_agent("alpha", "Hub", findings)
    store.save(first)
    time.sleep(1.05)

    second = _report_with_named_agent("alpha", "Hub", findings)
    comparison = store.compare_to_previous(second)

    assert comparison["first_run"] is False
    assert comparison["new_findings"] == []
    assert comparison["resolved_findings"] == []


def test_improved_topology_reports_resolved_findings(tmp_path: Path) -> None:
    """When a finding actually disappears on the next run, history surfaces it."""
    store = HistoryStore(tmp_path / "history")
    noisy = _report_with_named_agent(
        "alpha",
        "Hub",
        [
            _finding(Severity.CRITICAL, title="SPOF detected"),
            _finding(Severity.HIGH, title="High blast radius"),
        ],
    )
    store.save(noisy)
    time.sleep(1.05)

    # Improved topology: SPOF resolved, high-blast-radius remains.
    improved = _report_with_named_agent(
        "alpha",
        "Hub",
        [_finding(Severity.HIGH, title="High blast radius")],
    )
    comparison = store.compare_to_previous(improved)

    resolved_titles = {f["title"] for f in comparison["resolved_findings"]}
    assert resolved_titles == {"SPOF detected"}
    assert comparison["new_findings"] == []


# ---------------------------------------------------------------------------
# Swarm Score behaviour — regression test for the saturation bug.
# ---------------------------------------------------------------------------


def test_swarm_score_does_not_saturate_at_zero() -> None:
    """A handful of CRITICAL findings must not peg swarm_score to 0.

    Without this guarantee, topology improvements run-over-run are invisible
    because every run already scores 0. Three CRITICAL findings should leave
    well over 20 points of headroom for a less-noisy follow-up run.
    """
    report = _make_report(
        swarm_name="alpha",
        findings=[_finding(Severity.CRITICAL, title=f"Crit {i}") for i in range(3)],
    )
    assert report.swarm_score > 20


def test_redundant_topology_scores_higher_than_hub_spoke() -> None:
    """A redundant topology must out-score a fragile hub-spoke topology.

    The two reports carry identical findings — the only differentiator is the
    redundancy_scores map (low for the hub-spoke, high for the redundant one).
    """
    findings = [
        _finding(Severity.HIGH, title="High blast radius"),
        _finding(Severity.MEDIUM, title="Tight coupling"),
    ]
    hub_spoke = SwarmReport(
        swarm_name="hub",
        agent_count=6,
        edge_count=5,
        test_results=[
            TestResult(test_name="synthetic", status=TestStatus.FAILED, findings=findings)
        ],
        # Hub is irreplaceable, workers are partial peers.
        redundancy_scores={"hub": 5.0, "w1": 30.0, "w2": 30.0},
    )
    redundant = SwarmReport(
        swarm_name="redundant",
        agent_count=6,
        edge_count=8,
        test_results=[
            TestResult(test_name="synthetic", status=TestStatus.FAILED, findings=findings)
        ],
        # Two hubs share load — every agent has peers.
        redundancy_scores={"hub-a": 80.0, "hub-b": 80.0, "w1": 75.0, "w2": 75.0},
    )
    assert redundant.swarm_score > hub_spoke.swarm_score
