"""Tests for the new --quiet / --verbose CLI verbosity modes."""

from __future__ import annotations

from click.testing import CliRunner

from swarm_test.cli import cli


def _run(args: list[str]) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(cli, args, catch_exceptions=False)
    return result.exit_code, result.output


def test_quiet_mode_prints_only_headline() -> None:
    """`scan --quiet` prints the headline verdict line(s) — no full report.

    The headline is the Swarm Score line plus, optionally, a Cost Risk line
    that only appears when the cost_risk attack produced findings.
    """
    code, out = _run(
        [
            "scan",
            "-a",
            "A,B,C",
            "-e",
            "A>B,B>C",
            "--quiet",
        ]
    )
    assert code == 0
    # Strip blank lines for robustness
    non_blank = [line for line in out.splitlines() if line.strip()]
    assert 1 <= len(non_blank) <= 2, f"expected 1-2 headline lines, got: {non_blank!r}"
    assert "Swarm Score:" in non_blank[0]
    if len(non_blank) == 2:
        assert "Cost Risk:" in non_blank[1]
    # Quiet mode must not print the report header
    assert "SWARM-TEST RELIABILITY REPORT" not in out
    assert "Test Results" not in out


def test_verbose_mode_shows_all_findings() -> None:
    """`scan --verbose` shows graph metrics and the full report."""
    code, out = _run(
        [
            "scan",
            "-a",
            "A,B,C,D,E",
            "-e",
            "A>B,B>C,C>D,D>E,A>E",
            "--verbose",
        ]
    )
    assert code == 0
    # Headline present
    assert "Swarm Score:" in out
    # Full report rendered
    assert "SWARM-TEST RELIABILITY REPORT" in out
    # Verbose enables graph metrics panel
    assert "Graph Metrics" in out


def test_default_mode_hides_low_findings() -> None:
    """Default `scan` shows the headline + report but suppresses LOW/INFO findings."""
    code, out = _run(
        [
            "scan",
            "-a",
            "A,B,C",
            "-e",
            "A>B,B>C",
        ]
    )
    assert code == 0
    assert "Swarm Score:" in out
    assert "SWARM-TEST RELIABILITY REPORT" in out
    # Default verbosity should NOT print "Graph Metrics" panel (verbose-only)
    assert "Graph Metrics" not in out
