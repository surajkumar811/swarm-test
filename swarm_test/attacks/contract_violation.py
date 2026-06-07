"""Contract Violation Test — validate agent outputs against JSON Schema contracts."""

from __future__ import annotations

import logging
from typing import Any

from swarm_test.attacks.base import BaseAttack
from swarm_test.contracts.schema import (
    ContractRegistry,
    ContractResult,
    ContractViolation,
)
from swarm_test.core.models import Finding, Severity, TestResult, TestStatus

logger = logging.getLogger(__name__)


_VIOLATION_TYPE_TO_SEVERITY: dict[str, Severity] = {
    "missing_required": Severity.CRITICAL,
    "type_mismatch": Severity.HIGH,
    "unexpected_field": Severity.MEDIUM,
    "null_value": Severity.LOW,
    "schema_drift": Severity.MEDIUM,
}


class ContractViolationTest(BaseAttack):
    """Validates each agent's most recent intercepted output against its contract.

    For every registered role, the test pulls the latest payload that agent
    produced and checks it against ``ContractRegistry.validate_output()``.
    When a contract is scoped to a specific downstream role, only outputs sent
    along that edge are validated against the edge contract.

    Severity mapping:
        - missing_required → CRITICAL
        - type_mismatch    → HIGH
        - unexpected_field → MEDIUM
        - null_value       → LOW
        - schema_drift     → MEDIUM
    """

    name = "contract_violation"
    description = (
        "Validates agent outputs against registered JSON Schema contracts, "
        "flagging type mismatches, missing fields, and schema drift."
    )

    def __init__(self, registry: ContractRegistry) -> None:
        self.registry = registry

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _agent_role(graph: Any, agent_id: str) -> str:
        node = graph.graph.nodes.get(agent_id, {})
        return node.get("role", "unknown")

    @staticmethod
    def _agent_name(graph: Any, agent_id: str) -> str:
        node = graph.graph.nodes.get(agent_id, {})
        return node.get("name", agent_id)

    @staticmethod
    def _extract_output(event: Any) -> Any:
        """Pull the structured output payload from an intercepted event."""
        payload = event.payload or {}
        for key in ("output", "result", "response", "data"):
            if key in payload:
                return payload[key]
        if "result_repr" in payload:
            return payload["result_repr"]
        return payload or None

    def _latest_outputs_by_role(self, graph: Any) -> dict[str, tuple[Any, Any]]:
        """Return role → (latest_event, output) for the most recent output per role."""
        latest: dict[str, tuple[Any, Any]] = {}
        for event in graph.events:
            output = self._extract_output(event)
            if output is None:
                continue
            role = self._agent_role(graph, event.source_agent_id)
            if not role or role == "unknown":
                continue
            latest[role] = (event, output)
        return latest

    def _latest_outputs_by_edge(self, graph: Any) -> dict[tuple[str, str], tuple[Any, Any]]:
        """Return (src_role, dst_role) → (latest_event, output) per edge."""
        latest: dict[tuple[str, str], tuple[Any, Any]] = {}
        for event in graph.events:
            output = self._extract_output(event)
            if output is None:
                continue
            src_role = self._agent_role(graph, event.source_agent_id)
            dst_role = self._agent_role(graph, event.target_agent_id)
            if not src_role or src_role == "unknown":
                continue
            latest[(src_role, dst_role)] = (event, output)
        return latest

    def _violation_to_finding(
        self,
        result: ContractResult,
        violation: ContractViolation,
        event: Any,
        graph: Any,
    ) -> Finding:
        severity = _VIOLATION_TYPE_TO_SEVERITY.get(violation.violation_type, Severity.MEDIUM)
        src_name = self._agent_name(graph, event.source_agent_id)
        dst_name = self._agent_name(graph, event.target_agent_id) if event else ""
        edge_label = f" (→ {result.target_role})" if result.target_role else ""
        title = (
            f"Contract violation [{violation.violation_type}] in "
            f"{result.agent_role}{edge_label}: {violation.field}"
        )
        description = (
            f"Agent '{src_name}' (role={result.agent_role}) produced an output that "
            f"violates its contract at {violation.field}. "
            f"Expected: {violation.expected}. Actual: {violation.actual}."
        )
        affected: list[str] = [event.source_agent_id]
        if event.target_agent_id and event.target_agent_id != event.source_agent_id:
            affected.append(event.target_agent_id)
        return Finding(
            test_name=self.name,
            severity=severity,
            title=title,
            description=description,
            affected_agents=affected,
            evidence={
                "agent_role": result.agent_role,
                "target_role": result.target_role,
                "field": violation.field,
                "expected": violation.expected,
                "actual": violation.actual,
                "violation_type": violation.violation_type,
                "event_id": event.id,
                "source_agent": src_name,
                "target_agent": dst_name,
            },
            remediation=(
                "Update the agent's output formatting to match the contract, "
                "or update the contract to reflect the new expected shape."
            ),
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, graph: Any) -> TestResult:
        findings: list[Finding] = []
        metrics: dict[str, Any] = {
            "contracts_checked": 0,
            "contracts_valid": 0,
            "contracts_violated": 0,
            "score": 100.0,
            "violation_types": {},
        }

        if not self.registry.all_roles():
            return TestResult(
                test_name=self.name,
                status=TestStatus.PASSED,
                findings=[],
                metrics={"note": "No contracts registered"},
            )

        latest_by_role = self._latest_outputs_by_role(graph)
        latest_by_edge = self._latest_outputs_by_edge(graph)

        validations: list[ContractResult] = []

        for role in self.registry.all_roles():
            contract = self.registry.get_contract(role)
            if contract is None:
                continue

            # Role-level contract (no specific edge)
            if contract.output_schema:
                event_output = latest_by_role.get(role)
                if event_output is not None:
                    event, output = event_output
                    result = self.registry.validate_output(role, output)
                    validations.append(result)
                    metrics["contracts_checked"] += 1
                    if result.valid:
                        metrics["contracts_valid"] += 1
                    else:
                        metrics["contracts_violated"] += 1
                        for v in result.violations:
                            findings.append(self._violation_to_finding(result, v, event, graph))
                            counts = metrics["violation_types"]
                            counts[v.violation_type] = counts.get(v.violation_type, 0) + 1

            # Edge-level contracts
            for target_role in (contract.edge_contracts or {}).keys():
                event_output = latest_by_edge.get((role, target_role))
                if event_output is None:
                    continue
                event, output = event_output
                result = self.registry.validate_output(role, output, target_role=target_role)
                validations.append(result)
                metrics["contracts_checked"] += 1
                if result.valid:
                    metrics["contracts_valid"] += 1
                else:
                    metrics["contracts_violated"] += 1
                    for v in result.violations:
                        findings.append(self._violation_to_finding(result, v, event, graph))
                        counts = metrics["violation_types"]
                        counts[v.violation_type] = counts.get(v.violation_type, 0) + 1

        total = metrics["contracts_checked"]
        if total > 0:
            metrics["score"] = round(metrics["contracts_valid"] / total * 100, 2)
        else:
            metrics["score"] = 100.0
            metrics["note"] = "No agent outputs matched registered contract roles"

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,
            findings=findings,
            metrics=metrics,
        )
