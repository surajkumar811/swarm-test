"""Tests for the swarm-test plugin system."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from swarm_test import (
    AgentNode,
    EventType,
    Finding,
    InteractionEvent,
    Severity,
    SwarmProbe,
)
from swarm_test.config import SwarmConfig
from swarm_test.plugins import BasePlugin, PluginRegistry, PluginResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _GoodPlugin(BasePlugin):
    """Test plugin that always produces one HIGH finding."""

    name = "good_plugin"
    version = "0.1.0"
    description = "Test plugin"
    author = "tester"

    def run(self, graph, agents, edges, config) -> PluginResult:
        finding = Finding(
            test_name=self.name,
            severity=Severity.HIGH,
            title="Test finding",
            description="A test finding from a plugin",
            remediation="Fix it",
            affected_agents=[],
        )
        return PluginResult(
            test_name=self.name,
            status="failed",
            score=50.0,
            findings=[finding],
            duration_ms=1.0,
        )


class _BoomPlugin(BasePlugin):
    """Plugin whose run() always raises."""

    name = "boom_plugin"
    version = "0.1.0"
    description = "Always blows up"

    def run(self, graph, agents, edges, config) -> PluginResult:
        raise RuntimeError("kaboom")


def _make_simple_swarm() -> tuple[list[AgentNode], list[InteractionEvent]]:
    a = AgentNode(name="A", role="r1")
    b = AgentNode(name="B", role="r2")
    event = InteractionEvent(
        source_agent_id=a.id,
        target_agent_id=b.id,
        event_type=EventType.TASK_DELEGATE,
    )
    return [a, b], [event]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_base_plugin_abstract() -> None:
    """BasePlugin cannot be instantiated directly because run() is abstract."""
    with pytest.raises(TypeError):
        BasePlugin()  # type: ignore[abstract]


def test_plugin_registration() -> None:
    """register() stores plugin; get() retrieves it; list_plugins() exposes it."""
    reg = PluginRegistry()
    plugin = _GoodPlugin()
    reg.register(plugin)

    assert reg.get("good_plugin") is plugin
    assert "good_plugin" in reg
    assert len(reg) == 1
    listed = reg.list_plugins()
    assert listed == [
        {
            "name": "good_plugin",
            "version": "0.1.0",
            "description": "Test plugin",
            "author": "tester",
        }
    ]

    reg.unregister("good_plugin")
    assert reg.get("good_plugin") is None


def test_plugin_name_conflict() -> None:
    """Registering a plugin whose name matches a built-in test raises ValueError."""

    class _BuiltinCollision(BasePlugin):
        name = "cascade"  # collides with the built-in cascade test
        version = "0.0.1"
        description = "Bad"

        def run(self, graph, agents, edges, config) -> PluginResult:
            return PluginResult(test_name=self.name)

    reg = PluginRegistry()
    with pytest.raises(ValueError, match="conflicts with a built-in"):
        reg.register(_BuiltinCollision())


def test_plugin_discovery() -> None:
    """discover() loads BasePlugin classes from the entry-point group."""

    class _FakeEntryPoint:
        name = "good_plugin"

        def load(self):
            return _GoodPlugin

    reg = PluginRegistry()
    with patch(
        "swarm_test.plugins.registry.entry_points",
        return_value=[_FakeEntryPoint()],
    ):
        reg.discover()

    assert reg.get("good_plugin") is not None
    assert isinstance(reg.get("good_plugin"), _GoodPlugin)


def test_plugin_run() -> None:
    """run_all() executes a registered plugin and returns a PluginResult."""
    reg = PluginRegistry()
    reg.register(_GoodPlugin())

    agents, edges = _make_simple_swarm()
    probe = SwarmProbe(
        swarm_name="t",
        agents=agents,
        events=edges,
        plugin_registry=reg,
    )
    # Run plugins directly via the registry
    results = reg.run_all(probe.graph, agents, edges, None)
    assert len(results) == 1
    assert isinstance(results[0], PluginResult)
    assert results[0].test_name == "good_plugin"
    assert results[0].status == "failed"
    assert len(results[0].findings) == 1
    assert results[0].findings[0].severity == Severity.HIGH


def test_plugin_exception_handling(caplog) -> None:
    """A plugin that raises does not crash the run; the error is logged."""
    reg = PluginRegistry()
    reg.register(_BoomPlugin())
    reg.register(_GoodPlugin())

    agents, edges = _make_simple_swarm()
    probe = SwarmProbe(
        swarm_name="t",
        agents=agents,
        events=edges,
        plugin_registry=reg,
    )

    with caplog.at_level("ERROR", logger="swarm_test.core.probe"):
        report = probe.run_all()

    # Built-in tests + 2 plugins
    plugin_results = [
        r for r in report.test_results if r.test_name in {"boom_plugin", "good_plugin"}
    ]
    assert len(plugin_results) == 2
    boom = next(r for r in plugin_results if r.test_name == "boom_plugin")
    assert boom.status.value == "error"
    assert "kaboom" in (boom.error or "")
    good = next(r for r in plugin_results if r.test_name == "good_plugin")
    assert good.status.value == "failed"


def test_plugin_findings_in_report() -> None:
    """Plugin findings end up in SwarmReport.all_findings."""
    reg = PluginRegistry()
    reg.register(_GoodPlugin())

    agents, edges = _make_simple_swarm()
    probe = SwarmProbe(
        swarm_name="t",
        agents=agents,
        events=edges,
        plugin_registry=reg,
    )
    report = probe.run_all()

    titles = [f.title for f in report.all_findings if f.test_name == "good_plugin"]
    assert "Test finding" in titles
    test_names = [r.test_name for r in report.test_results]
    assert "good_plugin" in test_names


def test_plugin_respects_config_filter() -> None:
    """disabled_tests=[plugin_name] skips the plugin during run_all()."""
    reg = PluginRegistry()
    reg.register(_GoodPlugin())

    agents, edges = _make_simple_swarm()
    config = SwarmConfig(disabled_tests=["good_plugin"])
    probe = SwarmProbe(
        swarm_name="t",
        agents=agents,
        events=edges,
        plugin_registry=reg,
        config=config,
    )
    report = probe.run_all()

    plugin_results = [r for r in report.test_results if r.test_name == "good_plugin"]
    assert plugin_results == []
    assert not any(f.test_name == "good_plugin" for f in report.all_findings)
