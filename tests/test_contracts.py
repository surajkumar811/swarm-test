"""Tests for output contract validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm_test import (
    AgentNode,
    EventType,
    InteractionEvent,
    Severity,
    SwarmProbe,
)
from swarm_test.attacks.contract_violation import ContractViolationTest
from swarm_test.contracts.schema import (
    AgentContract,
    ContractRegistry,
    ContractResult,
    ContractViolation,
)
from swarm_test.core.graph import SwarmGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph_with_output(role: str, output: object, target_role: str = "writer") -> SwarmGraph:
    g = SwarmGraph()
    a = AgentNode(name="Source", role=role)
    b = AgentNode(name="Target", role=target_role)
    g.add_agent(a)
    g.add_agent(b)
    g.record_event(
        InteractionEvent(
            source_agent_id=a.id,
            target_agent_id=b.id,
            event_type=EventType.AGENT_RESPONSE,
            payload={"output": output},
        )
    )
    return g


# ---------------------------------------------------------------------------
# ContractRegistry
# ---------------------------------------------------------------------------


class TestContractRegistry:
    def test_register_and_get(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", {"type": "object"})
        contract = reg.get_contract("researcher")
        assert contract is not None
        assert isinstance(contract, AgentContract)
        assert contract.agent_role == "researcher"

    def test_register_edge_contract(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", {"type": "object", "required": ["summary"]})
        reg.register(
            "researcher",
            {"type": "object", "required": ["summary", "key_findings"]},
            target_role="analyst",
        )
        edge_contract = reg.get_contract("researcher", target_role="analyst")
        assert edge_contract is not None
        assert edge_contract.edge_contracts is not None
        assert "analyst" in edge_contract.edge_contracts

    def test_from_dict(self) -> None:
        data = {
            "contracts": {
                "researcher": {
                    "strict": True,
                    "output_schema": {
                        "type": "object",
                        "required": ["summary"],
                        "properties": {"summary": {"type": "string"}},
                    },
                    "edges": {
                        "writer": {
                            "type": "object",
                            "required": ["title"],
                        }
                    },
                }
            }
        }
        reg = ContractRegistry.from_dict(data)
        contract = reg.get_contract("researcher")
        assert contract is not None
        assert contract.strict is True
        edge = reg.get_contract("researcher", target_role="writer")
        assert edge is not None

    def test_from_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "contracts.yaml"
        p.write_text(
            "contracts:\n"
            "  writer:\n"
            "    output_schema:\n"
            "      type: object\n"
            "      required: [title]\n"
            "      properties:\n"
            "        title: {type: string}\n"
        )
        reg = ContractRegistry.from_yaml(str(p))
        assert reg.get_contract("writer") is not None


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------


class TestValidateOutput:
    def _schema(self) -> dict:
        return {
            "type": "object",
            "required": ["summary", "confidence"],
            "properties": {
                "summary": {"type": "string"},
                "confidence": {"type": "number"},
            },
        }

    def test_valid_output_passes(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", self._schema())
        result = reg.validate_output(
            "researcher",
            {"summary": "ok", "confidence": 0.9},
        )
        assert isinstance(result, ContractResult)
        assert result.valid is True
        assert result.violations == []

    def test_missing_required_field(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", self._schema())
        result = reg.validate_output("researcher", {"summary": "ok"})
        assert result.valid is False
        assert any(v.violation_type == "missing_required" for v in result.violations)
        v = next(v for v in result.violations if v.violation_type == "missing_required")
        assert v.actual == "missing"
        assert "confidence" in v.field

    def test_type_mismatch(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", self._schema())
        result = reg.validate_output(
            "researcher",
            {"summary": "ok", "confidence": "high"},
        )
        assert result.valid is False
        v = next(v for v in result.violations if v.violation_type == "type_mismatch")
        assert v.expected == "number"
        assert v.actual == "str"
        assert "confidence" in v.field

    def test_null_value_classification(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", self._schema())
        result = reg.validate_output(
            "researcher",
            {"summary": "ok", "confidence": None},
        )
        assert result.valid is False
        assert any(v.violation_type == "null_value" for v in result.violations)

    def test_unexpected_field_strict(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", self._schema(), strict=True)
        result = reg.validate_output(
            "researcher",
            {"summary": "ok", "confidence": 0.9, "extra": "boom"},
        )
        assert result.valid is False
        assert any(v.violation_type == "unexpected_field" for v in result.violations)

    def test_unknown_role_returns_valid(self) -> None:
        reg = ContractRegistry()
        result = reg.validate_output("nobody", {"x": 1})
        assert result.valid is True
        assert result.violations == []

    def test_edge_contract_used_for_target(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", {"type": "object"})
        reg.register(
            "researcher",
            {
                "type": "object",
                "required": ["key_findings"],
                "properties": {"key_findings": {"type": "array"}},
            },
            target_role="analyst",
        )
        # Without target_role: passes (base schema is permissive)
        assert reg.validate_output("researcher", {"x": 1}).valid is True
        # With target_role: stricter, fails
        result = reg.validate_output("researcher", {"x": 1}, target_role="analyst")
        assert result.valid is False
        assert any(v.violation_type == "missing_required" for v in result.violations)


# ---------------------------------------------------------------------------
# ContractViolationTest attack
# ---------------------------------------------------------------------------


class TestContractViolationAttack:
    def _registry(self) -> ContractRegistry:
        reg = ContractRegistry()
        reg.register(
            "researcher",
            {
                "type": "object",
                "required": ["summary", "confidence"],
                "properties": {
                    "summary": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        )
        return reg

    def test_no_contracts_passes(self) -> None:
        attack = ContractViolationTest(ContractRegistry())
        g = SwarmGraph()
        g.add_agent(AgentNode(name="A", role="researcher"))
        result = attack.run(g)
        assert result.findings == []
        assert result.metrics["note"] == "No contracts registered"

    def test_valid_output_no_findings(self) -> None:
        reg = self._registry()
        g = _graph_with_output(
            "researcher", {"summary": "ok", "confidence": 0.9}
        )
        attack = ContractViolationTest(reg)
        result = attack.run(g)
        assert result.findings == []
        assert result.metrics["contracts_valid"] == 1
        assert result.metrics["score"] == 100.0

    def test_missing_field_creates_critical(self) -> None:
        reg = self._registry()
        g = _graph_with_output("researcher", {"summary": "ok"})
        attack = ContractViolationTest(reg)
        result = attack.run(g)
        critical = [f for f in result.findings if f.severity == Severity.CRITICAL]
        assert len(critical) >= 1
        finding = critical[0]
        assert finding.test_name == "contract_violation"
        assert finding.evidence["violation_type"] == "missing_required"
        assert "confidence" in finding.evidence["field"]
        assert result.metrics["score"] == 0.0
        assert result.metrics["violation_types"].get("missing_required", 0) >= 1

    def test_type_mismatch_creates_high(self) -> None:
        reg = self._registry()
        g = _graph_with_output(
            "researcher", {"summary": "ok", "confidence": "high"}
        )
        attack = ContractViolationTest(reg)
        result = attack.run(g)
        high = [f for f in result.findings if f.severity == Severity.HIGH]
        assert len(high) >= 1
        assert high[0].evidence["violation_type"] == "type_mismatch"

    def test_edge_contract_validated(self) -> None:
        reg = ContractRegistry()
        reg.register("researcher", {"type": "object"})
        reg.register(
            "researcher",
            {
                "type": "object",
                "required": ["key_findings"],
            },
            target_role="analyst",
        )
        g = SwarmGraph()
        r = AgentNode(name="R", role="researcher")
        a = AgentNode(name="A", role="analyst")
        g.add_agent(r)
        g.add_agent(a)
        g.record_event(
            InteractionEvent(
                source_agent_id=r.id,
                target_agent_id=a.id,
                event_type=EventType.AGENT_RESPONSE,
                payload={"output": {"summary": "x"}},
            )
        )
        attack = ContractViolationTest(reg)
        result = attack.run(g)
        edge_findings = [
            f for f in result.findings if f.evidence.get("target_role") == "analyst"
        ]
        assert len(edge_findings) >= 1


# ---------------------------------------------------------------------------
# SwarmProbe integration
# ---------------------------------------------------------------------------


class TestSwarmProbeContracts:
    def test_probe_with_registry(self) -> None:
        reg = ContractRegistry()
        reg.register(
            "researcher",
            {
                "type": "object",
                "required": ["summary"],
                "properties": {"summary": {"type": "string"}},
            },
        )
        r = AgentNode(name="R", role="researcher")
        w = AgentNode(name="W", role="writer")
        probe = SwarmProbe(
            swarm_name="t",
            agents=[r, w],
            events=[
                InteractionEvent(
                    source_agent_id=r.id,
                    target_agent_id=w.id,
                    event_type=EventType.AGENT_RESPONSE,
                    payload={"output": {"summary": "ok"}},
                )
            ],
            contracts=reg,
        )
        report = probe.run_all()
        test_names = {r.test_name for r in report.test_results}
        assert "contract_violation" in test_names

    def test_probe_without_contracts_excludes_test(self) -> None:
        probe = SwarmProbe(
            swarm_name="t",
            agents=[AgentNode(name="A", role="researcher")],
        )
        report = probe.run_all()
        test_names = {r.test_name for r in report.test_results}
        assert "contract_violation" not in test_names

    def test_probe_with_yaml_path(self, tmp_path: Path) -> None:
        p = tmp_path / "contracts.yaml"
        p.write_text(
            "contracts:\n"
            "  researcher:\n"
            "    output_schema:\n"
            "      type: object\n"
            "      required: [summary]\n"
        )
        r = AgentNode(name="R", role="researcher")
        w = AgentNode(name="W", role="writer")
        probe = SwarmProbe(
            swarm_name="t",
            agents=[r, w],
            events=[
                InteractionEvent(
                    source_agent_id=r.id,
                    target_agent_id=w.id,
                    event_type=EventType.AGENT_RESPONSE,
                    payload={"output": {"other_field": "x"}},
                )
            ],
            contracts=str(p),
        )
        report = probe.run_all()
        contract_result = next(
            r for r in report.test_results if r.test_name == "contract_violation"
        )
        assert any(
            f.severity == Severity.CRITICAL for f in contract_result.findings
        )

    def test_invalid_contracts_type_raises(self) -> None:
        with pytest.raises(TypeError):
            SwarmProbe(
                swarm_name="t",
                agents=[AgentNode(name="A", role="researcher")],
                contracts=12345,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Violation dataclass roundtrip
# ---------------------------------------------------------------------------


def test_contract_violation_model() -> None:
    v = ContractViolation(
        field="$.confidence",
        expected="number",
        actual="string",
        violation_type="type_mismatch",
    )
    assert v.field == "$.confidence"
    assert v.violation_type == "type_mismatch"
