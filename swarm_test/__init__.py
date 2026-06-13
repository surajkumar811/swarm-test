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

__version__ = "0.3.0"
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
]
