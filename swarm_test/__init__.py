"""
swarm-test: The first reliability testing framework for multi-agent AI systems.

Quick start::

    from swarm_test import SwarmProbe

    probe = SwarmProbe(crew)
    report = probe.run_all()
    report.print_summary()
"""

from swarm_test.core.graph import SwarmGraph
from swarm_test.core.models import (
    AgentNode,
    EventType,
    Finding,
    InteractionEvent,
    Severity,
    SwarmReport,
    TestResult,
    TestStatus,
)
from swarm_test.core.probe import SwarmProbe
from swarm_test.plugins import BasePlugin, PluginRegistry, PluginResult, discover_plugins

__version__ = "0.3.6"
__author__ = "swarm-test contributors"
__license__ = "MIT"

__all__ = [
    # Main API
    "SwarmProbe",
    "SwarmGraph",
    # Models
    "AgentNode",
    "InteractionEvent",
    "Finding",
    "TestResult",
    "SwarmReport",
    "Severity",
    "EventType",
    "TestStatus",
    # Plugin system
    "BasePlugin",
    "PluginResult",
    "PluginRegistry",
    "discover_plugins",
]
