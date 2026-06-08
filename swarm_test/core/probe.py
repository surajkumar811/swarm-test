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
        config: Any | None = None,
        contracts: Any | None = None,
    ) -> None:
        self.swarm = swarm
        self.swarm_name = swarm_name
        self.graph = SwarmGraph()
        self._framework = framework or self._detect_framework(swarm)
        self._adapter: Any | None = None
        self.config = config
        self.contracts = self._resolve_contracts(contracts, config)

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

        self._attacks: list[Any] = self._load_attacks(config, self.contracts)

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
        # AutoGen detection: GroupChat / GroupChatManager / ConversableAgent variants
        if (
            "autogen" in module.lower()
            or "GroupChat" in cls_name
            or "GroupChatManager" in cls_name
            or cls_name in ("ConversableAgent", "AssistantAgent", "UserProxyAgent")
        ):
            return "autogen"
        # List of ConversableAgent-like objects
        if isinstance(swarm, (list, tuple)) and swarm:
            first = swarm[0]
            first_cls = type(first).__name__
            first_mod = type(first).__module__ or ""
            if (
                "autogen" in first_mod.lower()
                or first_cls in ("ConversableAgent", "AssistantAgent", "UserProxyAgent")
                or "ConversableAgent" in first_cls
            ):
                return "autogen"
        if "langgraph" in module.lower():
            return "langgraph"
        if "langchain" in module.lower():
            return "langchain"
        return "generic"

    def _load_adapter(self) -> Any | None:
        adapters = {
            "crewai": "swarm_test.integrations.crewai_adapter.CrewAIAdapter",
            "langgraph": "swarm_test.integrations.langgraph_adapter.LangGraphAdapter",
            "autogen": "swarm_test.integrations.autogen_adapter.AutoGenAdapter",
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
    def _resolve_contracts(contracts: Any | None, config: Any | None) -> Any | None:
        """Normalize ``contracts`` (str path | dict | ContractRegistry | None) to a registry."""
        from swarm_test.contracts.schema import ContractRegistry

        if contracts is None and config is not None:
            path = getattr(config, "contracts_path", None)
            if path:
                contracts = path

        if contracts is None:
            return None
        if isinstance(contracts, ContractRegistry):
            return contracts
        if isinstance(contracts, str):
            return ContractRegistry.from_yaml(contracts)
        if isinstance(contracts, dict):
            return ContractRegistry.from_dict(contracts)
        raise TypeError(
            f"contracts must be ContractRegistry | dict | str path | None, "
            f"got {type(contracts).__name__}"
        )

    @staticmethod
    def _load_attacks(config: Any | None = None, contracts: Any | None = None) -> list[Any]:
        from swarm_test.attacks.blast_radius import BlastRadiusAttack
        from swarm_test.attacks.cascade import CascadeFailureAttack
        from swarm_test.attacks.collusion import CollusionDetectionAttack
        from swarm_test.attacks.context_leakage import ContextLeakageAttack
        from swarm_test.attacks.contract_violation import ContractViolationTest
        from swarm_test.attacks.intent_drift import IntentDriftAttack
        from swarm_test.attacks.timeout_resilience import TimeoutResilienceAttack

        # Short test-name → attack instance
        all_attacks: dict[str, Any] = {
            "cascade": CascadeFailureAttack(),
            "context_leakage": ContextLeakageAttack(),
            "intent_drift": IntentDriftAttack(),
            "collusion": CollusionDetectionAttack(),
            "blast_radius": BlastRadiusAttack(),
            "timeout": TimeoutResilienceAttack(),
        }
        if contracts is not None:
            all_attacks["contract_violation"] = ContractViolationTest(contracts)

        if config is not None:
            active = config.active_test_names()
            # "sensitive_data" is folded into context_leakage
            if "sensitive_data" in active and "context_leakage" not in active:
                active = set(active) | {"context_leakage"}
            # contract_violation is implicitly enabled when contracts are provided
            if contracts is not None and "contract_violation" not in active:
                active = set(active) | {"contract_violation"}
            attacks = [a for name, a in all_attacks.items() if name in active]
        else:
            attacks = list(all_attacks.values())

        # Apply config-driven attack tuning
        if config is not None:
            extra_patterns = getattr(config, "sensitive_patterns", None) or []
            timeout_seconds = getattr(config, "timeout_seconds", None)
            for atk in attacks:
                # Extra sensitive patterns → ContextLeakageAttack scanner
                if isinstance(atk, ContextLeakageAttack) and extra_patterns:
                    try:
                        import re as _re

                        from swarm_test.attacks.context_leakage import (
                            _PatternDef,  # type: ignore[attr-defined]
                            _scanner,
                        )
                        from swarm_test.core.models import Severity

                        for pat in extra_patterns:
                            _scanner._patterns.append(
                                _PatternDef(
                                    name=f"Custom: {pat[:40]}",
                                    category="custom",
                                    severity=Severity.HIGH,
                                    regex=_re.compile(pat),
                                )
                            )
                    except Exception as exc:
                        logger.warning("Failed to add custom sensitive_patterns: %s", exc)
                # Timeout config → TimeoutResilienceAttack
                if isinstance(atk, TimeoutResilienceAttack) and timeout_seconds is not None:
                    setattr(atk, "timeout_seconds", float(timeout_seconds))

        return attacks

    # ------------------------------------------------------------------
    # Test execution
    # ------------------------------------------------------------------

    def run_all(self, *, timeout_per_test: float = 30.0) -> SwarmReport:
        """Run all 6 chaos tests and return the aggregated SwarmReport."""
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

        # Compute per-agent health scores
        from swarm_test.scoring.agent_health import AgentHealthScorer

        agent_scores = AgentHealthScorer().score_all(self.graph)

        report = SwarmReport(
            swarm_name=self.swarm_name,
            framework=self._framework,
            agent_count=self.graph.graph.number_of_nodes(),
            edge_count=self.graph.graph.number_of_edges(),
            test_results=results,
            graph_metrics=metrics,
            agent_scores=agent_scores,
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
            has_actionable = any(f.severity in fail_severities for f in result.findings)
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

    @staticmethod
    def check_thresholds(config: Any, report: SwarmReport) -> bool:
        """Return True if any finding meets/exceeds fail_on_severity or max_blast_radius."""
        from swarm_test.core.models import Severity

        # Severity threshold
        severity_order = ["critical", "high", "medium", "low", "info"]
        fail_on = getattr(config, "fail_on_severity", "critical")
        if fail_on != "none":
            try:
                threshold_idx = severity_order.index(fail_on)
            except ValueError:
                threshold_idx = 0
            for finding in report.all_findings:
                try:
                    f_idx = severity_order.index(finding.severity.value)
                except ValueError:
                    continue
                if f_idx <= threshold_idx:
                    return True

        # Blast radius threshold
        max_br = getattr(config, "max_blast_radius", 1.0)
        if max_br < 1.0:
            for finding in report.all_findings:
                evidence = finding.evidence or {}
                blast = 0.0
                if "impact_percentage" in evidence:
                    try:
                        blast = float(evidence["impact_percentage"]) / 100.0
                    except (TypeError, ValueError):
                        blast = 0.0
                elif "blast_radius" in evidence:
                    try:
                        blast = float(evidence["blast_radius"])
                    except (TypeError, ValueError):
                        blast = 0.0
                if blast > max_br:
                    return True

        # Strict mode: any LOW/INFO triggers a fail too
        if getattr(config, "strict", False) and report.all_findings:
            _ = Severity  # ensure import is meaningful
            return True

        return False
