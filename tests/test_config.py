"""Tests for swarm_test.config — YAML loading, validation, merging, thresholds."""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm_test import AgentNode, Finding, Severity, SwarmReport
from swarm_test.config import (
    VALID_SEVERITIES,
    SwarmConfig,
    load_config,
    merge_cli_args,
)
from swarm_test.core.models import TestResult, TestStatus
from swarm_test.core.probe import SwarmProbe


def test_load_defaults() -> None:
    """SwarmConfig() returns documented defaults for every field."""
    cfg = SwarmConfig()
    assert cfg.fail_on_severity == "critical"
    assert cfg.max_blast_radius == 1.0
    assert cfg.enabled_tests is None
    assert cfg.disabled_tests == []
    assert cfg.sensitive_patterns == []
    assert cfg.output_format == "console"
    assert cfg.output_path is None
    assert cfg.quick_scan is False
    assert cfg.timeout_seconds == 30.0
    assert cfg.strict is False
    assert set(VALID_SEVERITIES) == {
        "critical",
        "high",
        "medium",
        "low",
        "info",
        "none",
    }


def test_load_yaml_file(tmp_path: Path) -> None:
    """load_config(path) parses a YAML file and applies its values."""
    cfg_path = tmp_path / ".swarmtest.yml"
    cfg_path.write_text(
        "fail_on_severity: high\n"
        "max_blast_radius: 0.5\n"
        "disabled_tests:\n"
        "  - collusion\n"
        "sensitive_patterns:\n"
        "  - INTERNAL-[A-Z0-9]+\n"
        "output_format: json\n"
        "output_path: ./out.json\n"
        "quick_scan: true\n"
        "timeout_seconds: 10\n"
        "strict: true\n"
    )
    cfg = load_config(str(cfg_path))
    assert cfg.fail_on_severity == "high"
    assert cfg.max_blast_radius == 0.5
    assert cfg.disabled_tests == ["collusion"]
    assert cfg.sensitive_patterns == ["INTERNAL-[A-Z0-9]+"]
    assert cfg.output_format == "json"
    assert cfg.output_path == "./out.json"
    assert cfg.quick_scan is True
    assert cfg.timeout_seconds == 10.0
    assert cfg.strict is True


def test_cli_overrides_config() -> None:
    """merge_cli_args overrides config values; None CLI args don't override."""
    base = SwarmConfig(fail_on_severity="high", max_blast_radius=0.5, strict=False)

    # Only fail_on_severity provided; others None → not overridden
    overrides = {
        "fail_on_severity": "low",
        "max_blast_radius": None,
        "strict": None,
    }
    merged = merge_cli_args(base, overrides)
    assert merged.fail_on_severity == "low"
    assert merged.max_blast_radius == 0.5  # untouched
    assert merged.strict is False  # untouched

    # All overrides provided
    overrides2 = {
        "fail_on_severity": "medium",
        "max_blast_radius": 0.2,
        "strict": True,
    }
    merged2 = merge_cli_args(base, overrides2)
    assert merged2.fail_on_severity == "medium"
    assert merged2.max_blast_radius == 0.2
    assert merged2.strict is True


def test_invalid_severity_raises(tmp_path: Path) -> None:
    """fail_on_severity='banana' raises ValueError with a clear message."""
    cfg_path = tmp_path / ".swarmtest.yml"
    cfg_path.write_text("fail_on_severity: banana\n")
    with pytest.raises(ValueError) as exc_info:
        load_config(str(cfg_path))
    msg = str(exc_info.value)
    assert "fail_on_severity" in msg
    assert "banana" in msg
    # Lists the valid values
    assert "critical" in msg


def test_auto_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_config() with no path auto-discovers .swarmtest.yml in cwd."""
    cfg_path = tmp_path / ".swarmtest.yml"
    cfg_path.write_text("fail_on_severity: medium\nmax_blast_radius: 0.8\n")
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.fail_on_severity == "medium"
    assert cfg.max_blast_radius == 0.8


def test_disabled_tests_filter() -> None:
    """disabled_tests=['cascade'] excludes CascadeFailureAttack from the suite."""
    cfg = SwarmConfig(disabled_tests=["cascade"])
    probe = SwarmProbe(
        swarm_name="filter-test",
        agents=[AgentNode(name="A"), AgentNode(name="B")],
        config=cfg,
    )
    names = {type(a).__name__ for a in probe._attacks}
    assert "CascadeFailureAttack" not in names
    # Other attacks still present
    assert "BlastRadiusAttack" in names
    assert "ContextLeakageAttack" in names


def test_fail_on_severity_threshold() -> None:
    """check_thresholds returns True when a finding's severity meets the threshold."""
    cfg = SwarmConfig(fail_on_severity="high")
    report = SwarmReport(swarm_name="t")
    report.test_results = [
        TestResult(
            test_name="t",
            status=TestStatus.FAILED,
            findings=[
                Finding(
                    test_name="t",
                    severity=Severity.HIGH,
                    title="bad",
                    description="d",
                )
            ],
        )
    ]
    assert SwarmProbe.check_thresholds(cfg, report) is True

    # Below threshold → False
    cfg2 = SwarmConfig(fail_on_severity="critical")
    assert SwarmProbe.check_thresholds(cfg2, report) is False

    # "none" disables severity check entirely
    cfg3 = SwarmConfig(fail_on_severity="none")
    assert SwarmProbe.check_thresholds(cfg3, report) is False


def test_max_blast_radius_threshold() -> None:
    """check_thresholds returns True when finding blast_radius exceeds max."""
    cfg = SwarmConfig(fail_on_severity="none", max_blast_radius=0.3)
    report = SwarmReport(swarm_name="t")
    report.test_results = [
        TestResult(
            test_name="t",
            status=TestStatus.FAILED,
            findings=[
                Finding(
                    test_name="t",
                    severity=Severity.LOW,
                    title="big blast",
                    description="d",
                    evidence={"impact_percentage": 75.0},
                )
            ],
        )
    ]
    assert SwarmProbe.check_thresholds(cfg, report) is True

    # Lower blast radius → no failure
    cfg2 = SwarmConfig(fail_on_severity="none", max_blast_radius=0.9)
    assert SwarmProbe.check_thresholds(cfg2, report) is False
