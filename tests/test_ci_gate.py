"""Tests for the ``--ci`` gate mode on ``swarm-test run`` and ``swarm-test scan``.

Covers the contract a CI pipeline depends on:

* findings at/above the threshold  -> exit code 1 (fail the build)
* findings below the threshold      -> exit code 0 (pass)
* ``--ci`` defaults the threshold to ``high``
* an explicit flag or ``.swarmtest.yml`` value overrides the ``high`` default
* ``--output-format json`` emits a clean, parseable document on stdout
"""

from __future__ import annotations

import json
import subprocess
import sys

from click.testing import CliRunner

from swarm_test.cli import cli

# A cyclic X>Y>Z>X topology is an unbounded loop → CRITICAL + HIGH findings.
_BROKEN = ["-a", "X,Y,Z", "-e", "X>Y,Y>Z,Z>X"]
# A healthy declared-hub star: request out, response back. Only MEDIUM/INFO
# findings — no HIGH or CRITICAL — so it passes the default 'high' gate.
_HEALTHY = [
    "-a",
    "Hub:orchestrator,A,B,C",
    "-e",
    "Hub>A,Hub>B,Hub>C,A>Hub,B>Hub,C>Hub",
]


# ---------------------------------------------------------------------------
# run --ci
# ---------------------------------------------------------------------------


def test_run_ci_exceeds_threshold_exits_nonzero() -> None:
    """An unbounded loop trips the default 'high' threshold → exit 1."""
    result = CliRunner().invoke(cli, ["run", *_BROKEN, "--ci", "--no-history"])
    assert result.exit_code == 1, result.output


def test_run_ci_under_threshold_exits_zero() -> None:
    """fail-on-severity=none means nothing trips the gate → exit 0."""
    result = CliRunner().invoke(
        cli, ["run", *_BROKEN, "--ci", "--fail-on-severity", "none", "--no-history"]
    )
    assert result.exit_code == 0, result.output


def test_run_ci_prints_oneline_summary() -> None:
    """CI mode prints the concise Swarm Score headline for the CI log."""
    result = CliRunner().invoke(cli, ["run", *_BROKEN, "--ci", "--no-history"])
    assert "Swarm Score:" in result.output


# ---------------------------------------------------------------------------
# scan --ci
# ---------------------------------------------------------------------------


def test_scan_ci_exceeds_threshold_exits_nonzero() -> None:
    """scan --ci on a broken loop fails the build (previously scan exited 0)."""
    result = CliRunner().invoke(cli, ["scan", *_BROKEN, "--ci", "--no-history"])
    assert result.exit_code == 1, result.output


def test_scan_ci_healthy_declared_hub_exits_zero() -> None:
    """A healthy declared-hub star has no HIGH/CRITICAL findings → exit 0 at default 'high'.

    Regression for the gate UX flaw where a static scan's "no timing data"
    coverage gap was overstated as a HIGH timeout finding, making every healthy
    scan topology fail the CI gate. It is now INFO, so the gate passes.
    """
    result = CliRunner().invoke(cli, ["scan", *_HEALTHY, "--ci", "--no-history"])
    assert result.exit_code == 0, result.output


def test_scan_ci_default_threshold_is_high_not_medium() -> None:
    """The healthy hub's MEDIUM findings pass the default 'high' gate but trip a 'medium' gate.

    Proves the --ci default threshold is 'high': lowering it to 'medium' turns
    the same (otherwise-passing) topology into a failure.
    """
    at_high = CliRunner().invoke(cli, ["scan", *_HEALTHY, "--ci", "--no-history"])
    at_medium = CliRunner().invoke(
        cli, ["scan", *_HEALTHY, "--ci", "--fail-on-severity", "medium", "--no-history"]
    )
    assert at_high.exit_code == 0, at_high.output
    assert at_medium.exit_code == 1, at_medium.output


def test_scan_fail_on_alias_still_works() -> None:
    """The legacy --fail-on alias remains accepted."""
    result = CliRunner().invoke(
        cli, ["scan", "-a", "A,B,C,D", "-e", "A>B,B>C,C>D", "--fail-on", "medium", "--no-history"]
    )
    assert result.exit_code == 1, result.output


def test_scan_ci_json_output_is_parseable() -> None:
    """--output-format json emits a clean JSON document to stdout.

    Run as a real subprocess so stdout is genuinely separate from stderr
    (CliRunner's stdout/stderr handling varies across Click versions); the
    human-readable notices go to stderr, leaving stdout pure JSON.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "swarm_test.cli",
            "scan",
            *_BROKEN,
            "--ci",
            "--output-format",
            "json",
            "--no-history",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    diag = f"returncode={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert result.returncode == 1, diag
    # stdout must be pure, parseable JSON — no rich notices, and serialized with
    # a default=str fallback so numpy/networkx metric types don't crash it.
    data = json.loads(result.stdout)
    assert "findings" in data, diag


# ---------------------------------------------------------------------------
# config precedence — .swarmtest.yml must win over the --ci 'high' default
# ---------------------------------------------------------------------------


def test_ci_respects_config_fail_on_severity() -> None:
    """A .swarmtest.yml value overrides the --ci 'high' default."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open(".swarmtest.yml", "w") as f:
            f.write("fail_on_severity: none\n")
        result = runner.invoke(cli, ["run", *_BROKEN, "--ci", "--no-history"])
    # Config says 'none' → gate never fires even though the loop has criticals.
    assert result.exit_code == 0, result.output


def test_scan_ci_respects_config_fail_on_severity() -> None:
    """scan --ci also honours .swarmtest.yml over the 'high' default."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open(".swarmtest.yml", "w") as f:
            f.write("fail_on_severity: none\n")
        result = runner.invoke(cli, ["scan", *_BROKEN, "--ci", "--no-history"])
    assert result.exit_code == 0, result.output
