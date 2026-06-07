"""Output Contract Validation — JSON Schema contracts for agent outputs.

Define expected output shapes per agent role (and optionally per edge to a
specific downstream role), then validate intercepted outputs against them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

try:
    import jsonschema
    from jsonschema import Draft7Validator
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "jsonschema is required for swarm_test.contracts. " "Install with: pip install jsonschema"
    ) from exc


VIOLATION_TYPES = (
    "type_mismatch",
    "missing_required",
    "unexpected_field",
    "null_value",
    "schema_drift",
)


class AgentContract(BaseModel):
    """Contract describing the expected output shape for an agent role."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_role: str
    output_schema: dict[str, Any] = Field(default_factory=dict)
    edge_contracts: dict[str, dict[str, Any]] | None = None
    strict: bool = False


class ContractViolation(BaseModel):
    """A single contract violation."""

    field: str
    expected: str
    actual: str
    violation_type: str


class ContractResult(BaseModel):
    """Outcome of validating one output against a contract."""

    valid: bool
    violations: list[ContractViolation] = Field(default_factory=list)
    agent_role: str
    target_role: str | None = None


def _edge_key(agent_role: str, target_role: str | None) -> str:
    return f"{agent_role}->{target_role}" if target_role else agent_role


def _jsonpath_from_path(path: Any) -> str:
    """Convert a jsonschema error path (deque) to a `$.foo[0].bar` string."""
    parts = ["$"]
    for item in path:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            if parts[-1] == "$":
                parts.append(f".{item}")
            elif parts[-1].endswith("]"):
                parts.append(f".{item}")
            else:
                parts.append(f".{item}")
    return "".join(parts) if parts != ["$"] else "$"


def _classify_violation(error: jsonschema.ValidationError, output: Any) -> ContractViolation:
    """Map a jsonschema ValidationError to a ContractViolation."""
    field = _jsonpath_from_path(error.absolute_path)
    validator = error.validator
    expected = ""
    actual = ""
    violation_type = "schema_drift"

    if validator == "required":
        # jsonschema puts the missing field in the message; pull from validator_value
        missing = ""
        msg = error.message or ""
        # message like: "'foo' is a required property"
        if "'" in msg:
            try:
                missing = msg.split("'")[1]
            except IndexError:
                missing = ""
        field = (
            f"{field}.{missing}"
            if missing and field != "$"
            else (f"$.{missing}" if missing else field)
        )
        expected = "required"
        actual = "missing"
        violation_type = "missing_required"
    elif validator == "type":
        expected = (
            ",".join(error.validator_value)
            if isinstance(error.validator_value, list)
            else str(error.validator_value)
        )
        if error.instance is None:
            actual = "null"
            violation_type = "null_value"
        else:
            actual = type(error.instance).__name__
            violation_type = "type_mismatch"
    elif validator == "additionalProperties":
        # find the unexpected key from the message
        msg = error.message or ""
        unexpected = ""
        if "'" in msg:
            try:
                unexpected = msg.split("'")[1]
            except IndexError:
                unexpected = ""
        field = (
            f"{field}.{unexpected}"
            if unexpected and field != "$"
            else (f"$.{unexpected}" if unexpected else field)
        )
        expected = "no additional properties"
        actual = "unexpected field"
        violation_type = "unexpected_field"
    else:
        expected = str(error.validator_value)
        actual = repr(error.instance)
        violation_type = "schema_drift"

    return ContractViolation(
        field=field,
        expected=expected,
        actual=actual,
        violation_type=violation_type,
    )


class ContractRegistry:
    """Registry mapping agent roles (and edges) to JSON Schema contracts."""

    def __init__(self) -> None:
        self._contracts: dict[str, AgentContract] = {}

    # ------------------------------------------------------------------
    # Registration / lookup
    # ------------------------------------------------------------------

    def register(
        self,
        agent_role: str,
        schema: dict[str, Any],
        target_role: str | None = None,
        *,
        strict: bool = False,
    ) -> None:
        """Register a schema for an agent role, optionally scoped to an edge."""
        existing = self._contracts.get(agent_role)
        if existing is None:
            contract = AgentContract(
                agent_role=agent_role,
                output_schema=schema if target_role is None else {},
                edge_contracts={target_role: schema} if target_role else None,
                strict=strict,
            )
            self._contracts[agent_role] = contract
            return

        if target_role is None:
            existing.output_schema = schema
            existing.strict = strict or existing.strict
        else:
            edges = existing.edge_contracts or {}
            edges[target_role] = schema
            existing.edge_contracts = edges

    def get_contract(self, agent_role: str, target_role: str | None = None) -> AgentContract | None:
        contract = self._contracts.get(agent_role)
        if contract is None:
            return None
        if target_role is not None:
            edges = contract.edge_contracts or {}
            if target_role not in edges:
                return None
        return contract

    def all_roles(self) -> list[str]:
        return list(self._contracts.keys())

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractRegistry:
        """Build a registry from a dict shaped like the YAML schema.

        Expected shape::

            contracts:
              researcher:
                strict: false
                output_schema: { ... json schema ... }
                edges:
                  writer: { ... json schema for researcher->writer ... }
        """
        registry = cls()
        contracts = data.get("contracts", data)
        if not isinstance(contracts, dict):
            raise ValueError("Contracts file must contain a mapping of role -> contract.")

        for role, spec in contracts.items():
            if not isinstance(spec, dict):
                raise ValueError(f"Contract for role '{role}' must be a mapping.")
            strict = bool(spec.get("strict", False))
            output_schema = spec.get("output_schema") or spec.get("schema") or {}
            edges = spec.get("edges") or spec.get("edge_contracts") or {}
            if output_schema:
                if not isinstance(output_schema, dict):
                    raise ValueError(f"output_schema for '{role}' must be a mapping.")
                registry.register(role, output_schema, strict=strict)
            for target_role, edge_schema in edges.items():
                if not isinstance(edge_schema, dict):
                    raise ValueError(f"Edge contract '{role}->{target_role}' must be a mapping.")
                registry.register(role, edge_schema, target_role=target_role, strict=strict)
        return registry

    @classmethod
    def from_yaml(cls, path: str | Path) -> ContractRegistry:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Contracts file not found: {path}")
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Contracts file {path} must contain a YAML mapping.")
        return cls.from_dict(data)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_output(
        self,
        agent_role: str,
        output: Any,
        target_role: str | None = None,
    ) -> ContractResult:
        """Validate an agent's output against its registered contract."""
        contract = self._contracts.get(agent_role)
        if contract is None:
            return ContractResult(
                valid=True, violations=[], agent_role=agent_role, target_role=target_role
            )

        schema: dict[str, Any] = {}
        if target_role is not None and contract.edge_contracts:
            schema = contract.edge_contracts.get(target_role, {}) or contract.output_schema
        else:
            schema = contract.output_schema

        if not schema:
            return ContractResult(
                valid=True, violations=[], agent_role=agent_role, target_role=target_role
            )

        # Inject additionalProperties: false in strict mode if not specified
        effective_schema = dict(schema)
        if contract.strict and effective_schema.get("type") == "object":
            if "additionalProperties" not in effective_schema:
                effective_schema["additionalProperties"] = False

        try:
            validator = Draft7Validator(effective_schema)
        except jsonschema.SchemaError as exc:
            return ContractResult(
                valid=False,
                violations=[
                    ContractViolation(
                        field="$",
                        expected="valid JSON Schema",
                        actual=f"schema error: {exc.message}",
                        violation_type="schema_drift",
                    )
                ],
                agent_role=agent_role,
                target_role=target_role,
            )

        errors = sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
        violations = [_classify_violation(err, output) for err in errors]
        return ContractResult(
            valid=len(violations) == 0,
            violations=violations,
            agent_role=agent_role,
            target_role=target_role,
        )
