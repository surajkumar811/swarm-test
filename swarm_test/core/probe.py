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
from swarm_test.plugins.registry import PluginRegistry, discover_plugins

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
        plugin_registry: PluginRegistry | None = None,
        discover_plugins_on_init: bool = True,
        enable_history: bool | None = None,
    ) -> None:
        self.swarm = swarm
        self.swarm_name = swarm_name
        self.graph = SwarmGraph()
        self._framework = framework or self._detect_framework(swarm)
        self._adapter: Any | None = None
        self.config = config
        self.contracts = self._resolve_contracts(contracts, config)

        # Plugin registry — auto-discover unless caller injected one.
        if plugin_registry is not None:
            self.plugin_registry = plugin_registry
        elif discover_plugins_on_init:
            try:
                self.plugin_registry = discover_plugins()
            except Exception as exc:
                logger.warning("Plugin discovery failed: %s", exc)
                self.plugin_registry = PluginRegistry()
        else:
            self.plugin_registry = PluginRegistry()

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

        # Historical tracking. ``enable_history`` (constructor arg) wins over
        # config; with no override and no config, history is off so library
        # callers don't unexpectedly write to disk.
        self._history_dir: str = ".swarmtest-history"
        self._history_keep: int = 50
        if config is not None:
            self._history_dir = str(getattr(config, "history_dir", self._history_dir))
            self._history_keep = int(getattr(config, "history_keep", self._history_keep))
        if enable_history is not None:
            self._history_enabled = bool(enable_history)
        elif config is not None:
            self._history_enabled = bool(getattr(config, "history_enabled", False))
        else:
            self._history_enabled = False

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
        from swarm_test.attacks.cost_risk import CostRiskAttack
        from swarm_test.attacks.intent_drift import IntentDriftAttack
        from swarm_test.attacks.timeout_resilience import TimeoutResilienceAttack
        from swarm_test.attacks.trajectory import TrajectoryAttack

        # Short test-name → attack instance
        all_attacks: dict[str, Any] = {
            "cascade": CascadeFailureAttack(),
            "context_leakage": ContextLeakageAttack(),
            "intent_drift": IntentDriftAttack(),
            "collusion": CollusionDetectionAttack(),
            "blast_radius": BlastRadiusAttack(),
            "timeout": TimeoutResilienceAttack(),
            "trajectory_analysis": TrajectoryAttack(),
            "cost_risk": CostRiskAttack(),
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
            max_trajectory_depth = getattr(config, "max_trajectory_depth", None)
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
                # Trajectory config → TrajectoryAttack
                if isinstance(atk, TrajectoryAttack) and max_trajectory_depth is not None:
                    atk.max_trajectory_depth = int(max_trajectory_depth)

        return attacks

    # ------------------------------------------------------------------
    # Test execution
    # ------------------------------------------------------------------

    def run_all(self, *, timeout_per_test: float = 30.0) -> SwarmReport:
        """Run every registered built-in chaos test and return the aggregated SwarmReport."""
        started = datetime.now(timezone.utc)
        results: list[TestResult] = []

        logger.info(
            "SwarmProbe starting | framework=%s | agents=%d | events=%d",
            self._framework,
            self.graph.graph.number_of_nodes(),
            len(self.graph.events),
        )

        # Classify roles before any attack runs so attacks can read the role
        # context off the graph. Without this attacks emit CRITICAL findings
        # for intentional hubs (high-confidence orchestrators by design).
        try:
            self.graph.classify_roles()
        except Exception as exc:  # defensive — must never block attacks
            logger.warning("Role classification failed: %s", exc)

        for attack in self._attacks:
            result = self.run_test(attack)
            results.append(result)

        # Run plugins (third-party tests) and append their results.
        plugin_results = self._run_plugins()
        results.extend(plugin_results)

        metrics = self.graph.summary_metrics()

        # Compute per-agent health scores
        from swarm_test.scoring.agent_health import AgentHealthScorer

        agent_scores = AgentHealthScorer().score_all(self.graph)

        # Per-agent redundancy scores (0 = irreplaceable, 100 = fully redundant)
        redundancy_scores = self.graph.calculate_all_redundancy_scores()
        for agent_id, r_score in redundancy_scores.items():
            if agent_id in agent_scores:
                agent_scores[agent_id].redundancy_score = r_score

        # Reuse the pre-attack classification we attached to the graph.
        role_context = self.graph.role_context
        if role_context is None:
            from swarm_test.core.taxonomy import classify_all

            role_map_fallback = classify_all(
                self.graph.graph,
                agents=self.graph.agents,
                edges=self.graph.events,
            )
        else:
            role_map_fallback = role_context.role_map
        agent_roles: dict[str, dict[str, Any]] = {
            aid: {"role": role, "confidence": confidence}
            for aid, (role, confidence) in role_map_fallback.items()
        }
        for aid, info in agent_roles.items():
            agent_obj = self.graph.agents.get(aid)
            if agent_obj is not None:
                agent_obj.classified_role = info["role"]
                agent_obj.role_confidence = info["confidence"]

        report = SwarmReport(
            swarm_name=self.swarm_name,
            framework=self._framework,
            agent_count=self.graph.graph.number_of_nodes(),
            edge_count=self.graph.graph.number_of_edges(),
            test_results=results,
            graph_metrics=metrics,
            agent_scores=agent_scores,
            redundancy_scores=redundancy_scores,
            agent_roles=agent_roles,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )

        if self._history_enabled:
            try:
                from swarm_test.history import HistoryStore

                store = HistoryStore(self._history_dir)
                comparison = store.compare_to_previous(report)
                report.comparison = comparison
                store.save(report)
                store.prune(keep=self._history_keep)
            except Exception as exc:
                logger.warning("Historical tracking failed: %s", exc)

        return report

    def _run_plugins(self) -> list[TestResult]:
        """Run all discovered plugins, translating PluginResult → TestResult."""
        if len(self.plugin_registry) == 0:
            return []

        builtin_names = {getattr(a, "name", "") for a in self._attacks}
        enabled: list[str] | None = None
        disabled: set[str] = set()
        if self.config is not None:
            enabled = getattr(self.config, "enabled_tests", None)
            disabled = set(getattr(self.config, "disabled_tests", []) or [])

        agents = list(self.graph.agents.values())
        edges = list(self.graph.events)

        results: list[TestResult] = []
        for plugin in self.plugin_registry.plugins.values():
            # Skip plugins that collide with a built-in name (defensive — also
            # blocked at registration).
            if plugin.name in builtin_names:
                logger.warning(
                    "Plugin '%s' collides with built-in test name — skipping.",
                    plugin.name,
                )
                continue
            # Respect enabled_tests / disabled_tests
            if plugin.name in disabled:
                logger.info("Plugin '%s' disabled by config — skipping.", plugin.name)
                continue
            if enabled is not None and plugin.name not in enabled:
                logger.info("Plugin '%s' not in enabled_tests — skipping.", plugin.name)
                continue

            start = time.perf_counter()
            started_at = datetime.now(timezone.utc)
            try:
                presult = plugin.run(self.graph, agents, edges, self.config)
                if not hasattr(presult, "test_name") or not hasattr(presult, "findings"):
                    raise TypeError(f"Plugin {plugin.name}.run() must return a PluginResult")
                duration = (
                    presult.duration_ms
                    if presult.duration_ms > 0
                    else (time.perf_counter() - start) * 1000
                )
                status = (
                    TestStatus.PASSED
                    if str(presult.status).lower() == "passed"
                    else TestStatus.FAILED
                )
                results.append(
                    TestResult(
                        test_name=presult.test_name or plugin.name,
                        status=status,
                        duration_ms=duration,
                        findings=list(presult.findings),
                        metrics={
                            "plugin": True,
                            "plugin_version": plugin.version,
                            "plugin_author": plugin.author,
                            "score": presult.score,
                        },
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                logger.info(
                    "Plugin %s => %s (%d findings, %.1f ms)",
                    plugin.name,
                    status.value,
                    len(presult.findings),
                    duration,
                )
            except Exception as exc:
                logger.exception("Plugin %s raised an exception", plugin.name)
                results.append(
                    TestResult(
                        test_name=plugin.name,
                        status=TestStatus.ERROR,
                        duration_ms=(time.perf_counter() - start) * 1000,
                        findings=[],
                        metrics={"plugin": True, "plugin_version": plugin.version},
                        error=str(exc),
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
        return results

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
