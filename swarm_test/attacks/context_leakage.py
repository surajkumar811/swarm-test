"""Context Leakage Attack — detect sensitive data crossing agent boundaries."""

from __future__ import annotations

import logging
from typing import Any

from swarm_test.attacks.base import BaseAttack
from swarm_test.core.interceptor import check_sensitive_leakage
from swarm_test.core.models import Finding, Severity, TestResult, TestStatus

logger = logging.getLogger(__name__)

# Context keys that should never propagate between agents
_RESTRICTED_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "private_key",
        "credit_card",
        "ssn",
        "auth",
        "authorization",
        "bearer",
    }
)


class ContextLeakageAttack(BaseAttack):
    """
    Scans all recorded interaction events for sensitive data leakage
    patterns in payloads and result representations.

    Findings are raised when sensitive patterns (credentials, PII, secrets)
    are detected propagating across agent boundaries.
    """

    name = "context_leakage"
    description = (
        "Detects sensitive data (credentials, PII, secrets) leaking "
        "across agent context boundaries via interaction payloads."
    )

    def run(self, graph: Any) -> TestResult:
        findings: list[Finding] = []
        metrics: dict[str, Any] = {
            "events_scanned": 0,
            "leaks_detected": 0,
            "affected_edges": [],
        }

        events = graph.events
        metrics["events_scanned"] = len(events)

        if not events:
            return TestResult(
                test_name=self.name,
                status=TestStatus.PASSED,
                findings=[],
                metrics={"note": "No events recorded to scan"},
            )

        for event in events:
            leaks = self._scan_event(event)
            if leaks:
                metrics["leaks_detected"] += len(leaks)
                src_name = self._agent_name(graph, event.source_agent_id)
                dst_name = self._agent_name(graph, event.target_agent_id)

                edge_key = f"{event.source_agent_id}->{event.target_agent_id}"
                if edge_key not in metrics["affected_edges"]:
                    metrics["affected_edges"].append(edge_key)

                severity = Severity.CRITICAL if len(leaks) >= 2 else Severity.HIGH

                findings.append(
                    Finding(
                        test_name=self.name,
                        severity=severity,
                        title=f"Sensitive data leaked: {src_name} → {dst_name}",
                        description=(
                            f"Event {event.id} ({event.event_type.value}) "
                            f"from '{src_name}' to '{dst_name}' contains "
                            f"{len(leaks)} sensitive pattern(s) in its payload."
                        ),
                        affected_agents=[event.source_agent_id, event.target_agent_id],
                        evidence={
                            "event_id": event.id,
                            "event_type": event.event_type.value,
                            "patterns_matched": leaks,
                            "source_agent": src_name,
                            "target_agent": dst_name,
                        },
                        remediation=(
                            "Implement context isolation boundaries. Strip or mask "
                            "sensitive fields before passing context between agents. "
                            "Use an agent-level secrets manager."
                        ),
                    )
                )

        # Also scan for restricted keys in payload dicts
        self._scan_restricted_keys(graph, findings, metrics)

        return TestResult(
            test_name=self.name,
            status=TestStatus.PASSED,
            findings=findings,
            metrics=metrics,
        )

    @staticmethod
    def _scan_event(event: Any) -> list[str]:
        """Return list of sensitive pattern descriptions found in the event."""
        text_parts = []

        for key, value in event.payload.items():
            text_parts.append(f"{key}={value!r}")

        if event.error_message:
            text_parts.append(event.error_message)

        full_text = " ".join(text_parts)
        return check_sensitive_leakage(full_text)

    @staticmethod
    def _scan_restricted_keys(graph: Any, findings: list[Finding], metrics: dict[str, Any]) -> None:
        """Find events where payload dict keys suggest sensitive data transfer."""
        for event in graph.events:
            for key in event.payload:
                if key.lower() in _RESTRICTED_KEYS:
                    src_name = graph.graph.nodes.get(event.source_agent_id, {}).get(
                        "name", event.source_agent_id
                    )
                    dst_name = graph.graph.nodes.get(event.target_agent_id, {}).get(
                        "name", event.target_agent_id
                    )
                    metrics["leaks_detected"] += 1
                    findings.append(
                        Finding(
                            test_name="context_leakage",
                            severity=Severity.HIGH,
                            title=f"Restricted key '{key}' in agent context: {src_name} → {dst_name}",
                            description=(
                                f"The payload key '{key}' suggests sensitive data is being "
                                f"transferred from '{src_name}' to '{dst_name}'. "
                                "This key is on the restricted list."
                            ),
                            affected_agents=[event.source_agent_id, event.target_agent_id],
                            evidence={"key": key, "event_id": event.id},
                            remediation=(
                                "Remove or encrypt sensitive payload keys. "
                                "Use references/handles rather than raw secrets."
                            ),
                        )
                    )

    @staticmethod
    def _agent_name(graph: Any, agent_id: str) -> str:
        if agent_id in graph.graph.nodes:
            return graph.graph.nodes[agent_id].get("name", agent_id)
        return agent_id
