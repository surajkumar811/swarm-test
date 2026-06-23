"""Tests for the interactive HTML report.

These tests render a report and assert structural properties of the produced
HTML — they do not parse the JS, just check that the expected hooks, payloads,
and remediations are present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from swarm_test import (
    AgentNode,
    EventType,
    InteractionEvent,
    SwarmProbe,
)
from swarm_test.reporters.html import HtmlReporter


@pytest.fixture
def rendered_report(tmp_path: Path) -> tuple[str, Path]:
    """Run a swarm probe and render its HTML report. Returns (html, path)."""
    a = AgentNode(name="Researcher", role="researcher")
    b = AgentNode(name="Analyst", role="analyst")
    c = AgentNode(name="Writer", role="writer")

    probe = SwarmProbe(
        swarm_name="html-test-swarm",
        agents=[a, b, c],
        events=[
            InteractionEvent(
                source_agent_id=a.id,
                target_agent_id=b.id,
                event_type=EventType.TASK_DELEGATE,
                payload={"task": "analyse"},
            ),
            InteractionEvent(
                source_agent_id=b.id,
                target_agent_id=c.id,
                event_type=EventType.CONTEXT_SHARE,
                payload={"summary": "ready"},
            ),
            # Sensitive data leak — guarantees at least one finding with
            # a remediation arrow.
            InteractionEvent(
                source_agent_id=a.id,
                target_agent_id=b.id,
                event_type=EventType.CONTEXT_SHARE,
                payload={"creds": "AKIAIOSFODNN7EXAMPLE"},
            ),
        ],
    )
    report = probe.run_all()

    out = tmp_path / "report.html"
    reporter = HtmlReporter()
    reporter.render_with_graph(report, probe.graph, str(out))
    html = out.read_text(encoding="utf-8")
    return html, out


def test_html_report_contains_swarm_score(rendered_report: tuple[str, Path]) -> None:
    """The headline gauge shows the swarm_score and certification level."""
    html, _ = rendered_report
    # Score appears inside the .gauge-score div
    assert re.search(r"gauge-score[^>]*>\s*\d+\s*</div>", html), "score missing from gauge"
    # One of the certification levels must appear as a badge
    assert any(
        level in html for level in ("EXCELLENT", "GOOD", "NEEDS IMPROVEMENT", "AT RISK", "CRITICAL")
    )


def test_html_report_contains_agent_graph(rendered_report: tuple[str, Path]) -> None:
    """D3 force-directed graph code is embedded."""
    html, _ = rendered_report
    assert "d3.forceSimulation" in html
    assert "d3.forceLink" in html
    assert 'id="graph-container"' in html
    # Each agent's name should appear in the embedded JS payload
    assert "Researcher" in html
    assert "Analyst" in html
    assert "Writer" in html


def test_html_report_contains_heatmap(rendered_report: tuple[str, Path]) -> None:
    """The NxN interaction heatmap section is present."""
    html, _ = rendered_report
    assert 'id="heatmap"' in html
    assert "Interaction Heatmap" in html
    assert "heatmap-table" in html


def test_html_report_contains_findings(rendered_report: tuple[str, Path]) -> None:
    """Findings appear with their remediation arrow content."""
    html, _ = rendered_report
    # The sensitive-data leak should produce at least one finding card
    assert 'class="finding ' in html
    # Remediation block CSS class is used for actionable fixes
    assert "remediation" in html
    # The filter bar exposes the severity buttons
    assert 'id="filter-bar"' in html


def test_html_report_self_contained(rendered_report: tuple[str, Path]) -> None:
    """The report is self-contained except for the D3 CDN."""
    html, _ = rendered_report
    # Exactly one external resource: the D3 CDN. Comments and footer links
    # are fine — filter to actual asset loads (src= / href=).
    asset_loads = re.findall(r"""<(?:script|link|img)[^>]+(?:src|href)=["']([^"']+)["']""", html)
    for url in asset_loads:
        assert url.startswith("https://d3js.org/") or url.startswith(
            "#"
        ), f"unexpected external dependency: {url}"
    # No <link rel="stylesheet"> external imports
    assert 'rel="stylesheet"' not in html and "rel='stylesheet'" not in html
