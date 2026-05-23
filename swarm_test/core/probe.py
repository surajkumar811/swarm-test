"""SwarmProbe — the main entry point for swarm reliability testing."""

from __future__ import annotations

import importlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from swarm_test.core.graph import SwarmGraph
from swarm_test.core.models import (
    AgentNode,
    InteractionEvent,
    SwarmReport,
    TestResult,
    TestStatus,
)

logger = logging.getLogger(__name__)


class SwarmProbe:
    """
    Main entry point for swarm reliability testing.

    Usage::

        probe = SwarmProbe(crew)
        report = probe.run_all()
        report.print_summary()
    """

    def __init__(
        self,
        swarm: Any | None = None,
        *,
        swarm_name: str = "unnamed-swarm",
        agents: list[AgentNode] | None = None,
        events: list[InteractionEvent] | None = None,
        framework: str | None = None,
    ) -> None:
        self.swarm = swarm
        self.swarm_name = swarm_name
        self.graph = SwarmGraph()
        self._framework = framework or self._detect_framework(swarm)
        self._adapter: Any | None = None

        # Static graph fallback — supply agents/events directly
        if agents:
            for agent in agents:
                self.graph.add_agent(agent)
        if events:
            for event in events:
                self.graph.record_event(event)

        # Attach framework adapter if swarm provided
        if swarm is not None:
            self._adapter = self._load_adapter()
            if self._adapter:
                try:
                    self._adapter.ingest(swarm, self.graph)
                except Exception as exc:
                    logger.warning("Adapter ingest failed: %s", exc)

        self._attacks: list[Any] = self._load_attacks()

    # ------------------------------------------------------------------
    # Framework detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_framework(swarm: Any) -> str:
        if swarm is None:
            return "static"
        cls_name = type(swarm).__name__
        module = type(swarm).__module__ or ""

        if "crewai" in module.lower() or cls_name in ("Crew", "CrewAI"):
            return "crewai"
        if "autogen" in module.lower() or "GroupChat" in cls_name:
            return "autogen"
        if "langgraph" in module.lower():
            return "langgraph"
        if "langchain" in module.lower():
            return "langchain"
        return "generic"

    def _load_adapter(self) -> Any | None:
        adapters = {
            "crewai": "swarm_test.integrations.crewai_adapter.CrewAIAdapter",
            "generic": "swarm_test.integrations.base.BaseAdapter",
            "static": None,
        }
        adapter_path = adapters.get(self._framework, "swarm_test.integrations.base.BaseAdapter")
        if adapter_path is None:
            return None
        try:
            module_path, cls_name = adapter_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, cls_name)
            return cls()
        except Exception as exc:
            logger.warning("Could not load adapter for %s: %s", self._framework, exc)
            return None

    @staticmethod
    def _load_attacks() -> list[Any]:
        from swarm_test.attacks.blast_radius import BlastRadiusAttack
        from swarm_test.attacks.cascade import CascadeFailureAttack
        from swarm_test.attacks.collusion import CollusionDetectionAttack
        from swarm_test.attacks.context_leakage import ContextLeakageAttack
        from swarm_test.attacks.intent_drift import IntentDriftAttack

        return [
            CascadeFailureAttack(),
            ContextLeakageAttack(),
            IntentDriftAttack(),
            CollusionDetectionAttack(),
            BlastRadiusAttack(),
        ]

    # ------------------------------------------------------------------
    # Test execution
    # ------------------------------------------------------------------

    def run_all(self, *, timeout_per_test: float = 30.0) -> SwarmReport:
        """Run all 5 chaos tests and return the aggregated SwarmReport."""
        started = datetime.now(timezone.utc)
        results: list[TestResult] = []

        logger.info(
            "SwarmProbe starting | framework=%s | agents=%d | events=%d",
            self._framework,
            self.graph.graph.number_of_nodes(),
            len(self.graph.events),
        )

        for attack in self._attacks:
            result = self.run_test(attack)
            results.append(result)

        metrics = self.graph.summary_metrics()
        report = SwarmReport(
            swarm_name=self.swarm_name,
            framework=self._framework,
            agent_count=self.graph.graph.number_of_nodes(),
            edge_count=self.graph.graph.number_of_edges(),
            test_results=results,
            graph_metrics=metrics,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )
        return report

    def run_test(self, attack: Any) -> TestResult:
        """Run a single attack/test against the current graph."""
        test_name = getattr(attack, "name", type(attack).__name__)
        logger.info("Running test: %s", test_name)
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc)

        try:
            result: TestResult = attack.run(self.graph)
            result.duration_ms = (time.perf_counter() - start) * 1000
            result.started_at = started_at
            result.completed_at = datetime.now(timezone.utc)
            from swarm_test.core.models import Severity

            fail_severities = {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM}
            has_actionable = any(
                f.severity in fail_severities for f in result.findings
            )
            result.status = TestStatus.FAILED if has_actionable else TestStatus.PASSED
        except Exception as exc:
            logger.exception("Test %s raised an exception", test_name)
            result = TestResult(
                test_name=test_name,
                status=TestStatus.ERROR,
                duration_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        logger.info(
            "Test %s => %s (%d findings, %.1f ms)",
            test_name,
            result.status.value,
            len(result.findings),
            result.duration_ms,
        )
        return result

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def add_agent(self, agent: AgentNode) -> SwarmProbe:
        self.graph.add_agent(agent)
        return self

    def record_event(self, event: InteractionEvent) -> SwarmProbe:
        self.graph.record_event(event)
        return self

    @property
    def framework(self) -> str:
        return self._framework
