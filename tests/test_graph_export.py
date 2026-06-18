"""Tests for swarm_test.reporters.graph_export — Mermaid, DOT, and PNG exports."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from swarm_test import AgentNode, EventType, InteractionEvent
from swarm_test.core.graph import SwarmGraph
from swarm_test.core.probe import SwarmProbe
from swarm_test.reporters import graph_export


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def linear_swarm() -> tuple[SwarmProbe, list[AgentNode], list[InteractionEvent]]:
    """A → B → C linear pipeline. B is a SPOF (articulation point)."""
    a = AgentNode(name="Researcher", role="researcher")
    b = AgentNode(name="Analyst", role="analyst")
    c = AgentNode(name="Writer", role="writer")
    events = [
        InteractionEvent(
            source_agent_id=a.id, target_agent_id=b.id, event_type=EventType.TASK_DELEGATE
        ),
        InteractionEvent(
            source_agent_id=b.id, target_agent_id=c.id, event_type=EventType.TASK_DELEGATE
        ),
    ]
    probe = SwarmProbe(swarm_name="test-swarm", agents=[a, b, c], events=events)
    return probe, [a, b, c], events


def _run(probe: SwarmProbe):
    """Run the probe and return (graph, agents, edges, report)."""
    report = probe.run_all()
    return probe.graph, list(probe.graph.agents.values()), list(probe.graph.events), report


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


def test_mermaid_export_basic(linear_swarm):
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)
    out = graph_export.to_mermaid(g, agents, edges, report)
    assert isinstance(out, str)
    # Mermaid header
    assert "graph TD" in out
    # All three agents appear in the output
    for name in ("Researcher", "Analyst", "Writer"):
        assert name in out
    # classDef styling block is present
    assert "classDef spof" in out
    assert "classDef healthy" in out
    assert "classDef moderate" in out


def test_mermaid_spof_styling(linear_swarm):
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)
    out = graph_export.to_mermaid(g, agents, edges, report)
    # B (Analyst) is the articulation point in A → B → C
    spofs = g.find_single_points_of_failure()
    assert len(spofs) >= 1
    # At least one node line carries the ":::spof" class
    assert ":::spof" in out
    # SPOF nodes are annotated with the warning label
    assert "SPOF" in out


def test_mermaid_contains_all_edges(linear_swarm):
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)
    out = graph_export.to_mermaid(g, agents, edges, report)
    # Two edges in A → B → C — count "-->" arrows
    arrow_lines = [ln for ln in out.splitlines() if " --> " in ln]
    assert len(arrow_lines) == 2


# ---------------------------------------------------------------------------
# DOT
# ---------------------------------------------------------------------------


def test_dot_export_basic(linear_swarm):
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)
    out = graph_export.to_dot(g, agents, edges, report)
    assert isinstance(out, str)
    assert out.lstrip().startswith("digraph")
    assert "rankdir=TB" in out
    # Closes with brace
    assert out.rstrip().endswith("}")


def test_dot_spof_styling(linear_swarm):
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)
    out = graph_export.to_dot(g, agents, edges, report)
    # SPOF nodes are filled red (#ff4444)
    assert "#ff4444" in out
    # And labeled [SPOF]
    assert "[SPOF]" in out


def test_dot_contains_all_nodes(linear_swarm):
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)
    out = graph_export.to_dot(g, agents, edges, report)
    for name in ("Researcher", "Analyst", "Writer"):
        assert name in out
    # Two directed edges
    edge_lines = [ln for ln in out.splitlines() if "->" in ln and "{" not in ln]
    # Filter out comment / subgraph lines — only edge statements end with ';'
    real_edges = [ln for ln in edge_lines if ln.strip().endswith(";")]
    assert len(real_edges) == 2


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------


def test_png_export_creates_file(tmp_path: Path, linear_swarm):
    pytest.importorskip("matplotlib")
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)
    out_path = tmp_path / "graph.png"
    result = graph_export.to_png(g, agents, edges, report, str(out_path))
    assert result is True
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_png_requires_matplotlib(tmp_path: Path, linear_swarm):
    probe, _, _ = linear_swarm
    g, agents, edges, report = _run(probe)

    # Simulate matplotlib missing by patching the import machinery
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "matplotlib" or name.startswith("matplotlib."):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        # Drop any cached matplotlib references so the import is re-attempted
        for mod in list(sys.modules):
            if mod == "matplotlib" or mod.startswith("matplotlib."):
                sys.modules.pop(mod, None)
        with pytest.raises(ImportError, match="swarm-test\\[png\\]"):
            graph_export.to_png(g, agents, edges, report, str(tmp_path / "x.png"))
