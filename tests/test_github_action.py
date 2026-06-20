"""Tests for the GitHub Action integration (annotation + step-summary output)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from swarm_test import (
    AgentNode,
    EventType,
    Finding,
    InteractionEvent,
    Severity,
    SwarmReport,
)
from swarm_test import TestResult as _TestResult
from swarm_test import TestStatus as _TestStatus
from swarm_test.reporters.github import (
    GitHubReporter,
    certification_level,
    format_annotation,
    swarm_score,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_finding(severity: Severity, title: str = "Risk found") -> Finding:
    return Finding(
        test_name="cascade_failure",
        severity=severity,
        title=title,
        description="Failure propagates downstream",
        affected_agents=["agent-1"],
        remediation="Add a fallback path",
    )


def _make_report(severities: list[Severity]) -> SwarmReport:
    findings = [_make_finding(s, f"Finding {i}") for i, s in enumerate(severities)]
    status = (
        _TestStatus.FAILED
        if any(s in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM) for s in severities)
        else _TestStatus.PASSED
    )
    result = _TestResult(
        test_name="cascade_failure",
        status=status,
        duration_ms=10.0,
        findings=findings,
    )
    return SwarmReport(
        swarm_name="test-swarm",
        framework="generic",
        agent_count=3,
        edge_count=2,
        test_results=[result],
        graph_metrics={"single_points_of_failure": 0},
    )


@pytest.fixture
def cli_run_args(tmp_path: Path) -> list[str]:
    """A minimal `swarm-test run` invocation that produces no critical findings."""
    return [
        sys.executable,
        "-m",
        "swarm_test.cli",
        "run",
        "--agents",
        "A,B,C",
        "--edges",
        "A>B,B>C",
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_github_annotation_format() -> None:
    """Critical findings render as ``::error::`` workflow commands."""
    finding = _make_finding(Severity.CRITICAL, "Cascade SPOF")
    line = format_annotation(finding)
    assert line.startswith("::error ")
    assert "Cascade SPOF" in line
    assert "cascade_failure" in line


def test_github_warning_format() -> None:
    """High findings render as ``::warning::`` workflow commands."""
    finding = _make_finding(Severity.HIGH, "Risk handoff")
    line = format_annotation(finding)
    assert line.startswith("::warning ")
    assert "Risk handoff" in line


def test_github_notice_format() -> None:
    """Medium / low / info findings render as ``::notice::`` workflow commands."""
    for sev in (Severity.MEDIUM, Severity.LOW, Severity.INFO):
        line = format_annotation(_make_finding(sev))
        assert line.startswith("::notice "), f"{sev} should map to notice"


def test_github_step_summary(tmp_path: Path) -> None:
    """The step summary produces a valid markdown document with the headline metrics."""
    report = _make_report([Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM])
    summary = GitHubReporter().render_summary(report)

    # Headers & key metrics
    assert summary.startswith("## swarm-test —")
    assert "Swarm Score" in summary
    assert "Test Results" in summary
    assert "cascade_failure" in summary
    assert "Top findings" in summary
    # Markdown table syntax
    assert "| Metric | Value |" in summary
    assert "|--------|-------|" in summary


def test_github_step_summary_written_to_env_file(tmp_path: Path) -> None:
    """write_step_summary appends to ``$GITHUB_STEP_SUMMARY``."""
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("# existing content\n")
    report = _make_report([Severity.HIGH])
    os.environ["GITHUB_STEP_SUMMARY"] = str(summary_file)
    try:
        target = GitHubReporter().write_step_summary(report)
    finally:
        del os.environ["GITHUB_STEP_SUMMARY"]
    assert target == str(summary_file)
    content = summary_file.read_text()
    assert "# existing content" in content  # preserved
    assert "swarm-test" in content  # appended


def test_swarm_score_and_certification() -> None:
    """swarm_score reflects finding severity and maps to known certification bands."""
    clean = _make_report([])
    assert swarm_score(clean) == 100.0
    assert certification_level(swarm_score(clean)) == "Production-ready"

    # Enough criticals to land squarely in the "Critical risk" band under the
    # current (lighter) per-finding penalty curve used by SwarmReport.swarm_score.
    risky = _make_report([Severity.CRITICAL] * 4 + [Severity.HIGH] * 2)
    assert swarm_score(risky) < 50.0
    assert certification_level(swarm_score(risky)) == "Critical risk"


def test_exit_code_pass(cli_run_args: list[str], tmp_path: Path) -> None:
    """fail_on_severity=none disables threshold enforcement → exit 0."""
    args = [*cli_run_args, "--github-action", "--fail-on-severity", "none"]
    env = {**os.environ, "GITHUB_ACTIONS": ""}
    result = subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path
    )
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_exit_code_fail(cli_run_args: list[str], tmp_path: Path) -> None:
    """A linear A>B>C topology surfaces SPOF/cascade findings → exit 1."""
    args = [*cli_run_args, "--github-action", "--fail-on-severity", "critical"]
    env = {**os.environ, "GITHUB_ACTIONS": ""}
    result = subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path
    )
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_github_action_env_detection(cli_run_args: list[str], tmp_path: Path) -> None:
    """GITHUB_ACTIONS=true alone enables annotation output (no --github-action flag)."""
    summary_file = tmp_path / "step.md"
    env = {
        **os.environ,
        "GITHUB_ACTIONS": "true",
        "GITHUB_STEP_SUMMARY": str(summary_file),
    }
    # Use --fail-on-severity none so we can assert the summary even on clean runs.
    args = [*cli_run_args, "--fail-on-severity", "none"]
    result = subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path
    )
    assert result.returncode in (0, 1)
    assert summary_file.exists()
    assert "swarm-test" in summary_file.read_text()
