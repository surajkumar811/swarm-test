"""Tests for swarm_test.history.HistoryStore."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from swarm_test import Finding, Severity, SwarmReport, TestResult, TestStatus
from swarm_test.history import HistoryStore


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
    assert timestamps == sorted(timestamps, reverse=True), (
        "Entries should be newest-first"
    )


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
