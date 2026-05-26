"""Context Leakage Attack — detect sensitive data crossing agent boundaries."""

from __future__ import annotations

import logging
import re
from typing import Any

from swarm_test.attacks.base import BaseAttack
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


# ---------------------------------------------------------------------------
# SensitiveDataScanner — 20+ pattern types
# ---------------------------------------------------------------------------


class _PatternDef:
    """A single sensitive-data pattern with metadata."""

    __slots__ = ("name", "category", "severity", "regex")

    def __init__(self, name: str, category: str, severity: Severity, regex: re.Pattern) -> None:
        self.name = name
        self.category = category
        self.severity = severity
        self.regex = regex


def _luhn_check(digits: str) -> bool:
    """Validate a credit card number using the Luhn algorithm."""
    nums = [int(d) for d in digits if d.isdigit()]
    if len(nums) < 13:
        return False
    checksum = 0
    reverse = nums[::-1]
    for i, n in enumerate(reverse):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


# All patterns, grouped by category
_PATTERNS: list[_PatternDef] = [
    # --- Cloud API Keys ---
    _PatternDef(
        "AWS Access Key",
        "cloud_api_key",
        Severity.CRITICAL,
        re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    ),
    _PatternDef(
        "AWS Secret Key",
        "cloud_api_key",
        Severity.CRITICAL,
        re.compile(r"(?i)aws[_\-\s]*secret[_\-\s]*(?:access)?[_\-\s]*key\s*[:=]\s*\S+"),
    ),
    _PatternDef(
        "OpenAI API Key",
        "cloud_api_key",
        Severity.CRITICAL,
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
    ),
    _PatternDef(
        "Stripe Live Key",
        "cloud_api_key",
        Severity.CRITICAL,
        re.compile(r"sk_live_[A-Za-z0-9]{20,}"),
    ),
    _PatternDef(
        "Stripe Test Key",
        "cloud_api_key",
        Severity.HIGH,
        re.compile(r"sk_test_[A-Za-z0-9]{20,}"),
    ),
    _PatternDef(
        "Google Cloud API Key",
        "cloud_api_key",
        Severity.CRITICAL,
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    ),
    _PatternDef(
        "Azure Subscription Key",
        "cloud_api_key",
        Severity.CRITICAL,
        re.compile(r"(?i)(?:azure|subscription)[_\-\s]*key\s*[:=]\s*[A-Za-z0-9]{32,}"),
    ),
    # --- Authentication ---
    _PatternDef(
        "JWT Token",
        "authentication",
        Severity.CRITICAL,
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
    ),
    _PatternDef(
        "Bearer Token",
        "authentication",
        Severity.CRITICAL,
        re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    ),
    _PatternDef(
        "Basic Auth Header",
        "authentication",
        Severity.CRITICAL,
        re.compile(r"(?i)basic\s+[A-Za-z0-9+/]{10,}={0,2}"),
    ),
    _PatternDef(
        "Password Assignment",
        "authentication",
        Severity.CRITICAL,
        re.compile(r"(?i)(?:password|passwd|pwd)\s*[:=]\s*\S+"),
    ),
    _PatternDef(
        "Generic API Key Assignment",
        "authentication",
        Severity.CRITICAL,
        re.compile(r"(?i)(?:api[_\-]?key|apikey|token|secret)\s*[:=]\s*\S+"),
    ),
    # --- Financial ---
    _PatternDef(
        "Credit Card Number",
        "financial",
        Severity.CRITICAL,
        # 13-19 digits, optionally separated by spaces or dashes
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    ),
    _PatternDef(
        "Bank Account Pattern",
        "financial",
        Severity.HIGH,
        re.compile(r"(?i)(?:account|acct)[_\-\s]*(?:number|num|no|#)\s*[:=]\s*\d{8,17}"),
    ),
    # --- Personal / PII ---
    _PatternDef(
        "SSN",
        "pii",
        Severity.HIGH,
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    _PatternDef(
        "Email Address",
        "pii",
        Severity.HIGH,
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    ),
    _PatternDef(
        "Phone Number",
        "pii",
        Severity.HIGH,
        re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    ),
    # --- Secrets ---
    _PatternDef(
        "Private Key Block",
        "secrets",
        Severity.CRITICAL,
        re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE KEY-----"),
    ),
    _PatternDef(
        "Database Connection String",
        "secrets",
        Severity.CRITICAL,
        re.compile(r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql)://[^\s\"']{5,}"),
    ),
    _PatternDef(
        "Env File Pattern",
        "secrets",
        Severity.HIGH,
        re.compile(
            r"(?i)(?:DATABASE_URL|DB_PASSWORD|SECRET_KEY|PRIVATE_KEY|"
            r"API_SECRET|ENCRYPTION_KEY)\s*=\s*\S+"
        ),
    ),
    # --- Internal Infrastructure ---
    _PatternDef(
        "Internal IP (RFC1918)",
        "internal",
        Severity.MEDIUM,
        re.compile(
            r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        ),
    ),
    _PatternDef(
        "Localhost Reference",
        "internal",
        Severity.MEDIUM,
        re.compile(r"(?i)(?:https?://)?localhost(?::\d+)?(?:/\S*)?"),
    ),
    _PatternDef(
        "Sensitive File Path",
        "internal",
        Severity.MEDIUM,
        re.compile(r"(?:/etc/(?:passwd|shadow|hosts|ssl|ssh)|/home/[a-z_]\w*/\.\w+)"),
    ),
]


class SensitiveDataScanner:
    """
    Comprehensive scanner for 20+ sensitive data patterns in text.

    Each match returns a dict with:
      - pattern_type: human-readable name (e.g. "AWS Access Key")
      - category: grouping (cloud_api_key, authentication, financial, pii, secrets, internal)
      - severity: Severity enum value
      - matched: the matched text (truncated for safety)
    """

    def __init__(self) -> None:
        self._patterns = list(_PATTERNS)

    def scan(self, text: str) -> list[dict[str, Any]]:
        """Scan *text* and return a list of match dicts."""
        if not text:
            return []
        results: list[dict[str, Any]] = []
        seen_types: set[str] = set()
        for pdef in self._patterns:
            m = pdef.regex.search(text)
            if m is None:
                continue
            # For credit cards, apply Luhn validation
            if pdef.name == "Credit Card Number":
                raw = re.sub(r"[^\d]", "", m.group())
                if len(raw) < 13 or not _luhn_check(raw):
                    continue
            # Deduplicate by pattern name per scan
            if pdef.name in seen_types:
                continue
            seen_types.add(pdef.name)
            matched_text = m.group()
            results.append(
                {
                    "pattern_type": pdef.name,
                    "category": pdef.category,
                    "severity": pdef.severity,
                    "matched": (
                        matched_text[:60] + "..." if len(matched_text) > 60 else matched_text
                    ),
                }
            )
        return results


# Module-level scanner instance
_scanner = SensitiveDataScanner()


def scan_text(text: str) -> list[dict[str, Any]]:
    """Convenience function wrapping the default scanner."""
    return _scanner.scan(text)


# ---------------------------------------------------------------------------
# Attack
# ---------------------------------------------------------------------------


class ContextLeakageAttack(BaseAttack):
    """
    Scans all recorded interaction events for sensitive data leakage
    patterns in payloads and result representations.

    Uses SensitiveDataScanner (20+ pattern types) to detect credentials,
    PII, secrets, and internal infrastructure references crossing agent
    boundaries.
    """

    name = "context_leakage"
    description = (
        "Detects sensitive data (credentials, PII, secrets, internal infra) leaking "
        "across agent context boundaries via interaction payloads."
    )

    def run(self, graph: Any) -> TestResult:
        findings: list[Finding] = []
        metrics: dict[str, Any] = {
            "events_scanned": 0,
            "leaks_detected": 0,
            "affected_edges": [],
            "pattern_types_found": [],
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
            matches = self._scan_event(event)
            if matches:
                metrics["leaks_detected"] += len(matches)
                src_name = self._agent_name(graph, event.source_agent_id)
                dst_name = self._agent_name(graph, event.target_agent_id)

                edge_key = f"{event.source_agent_id}->{event.target_agent_id}"
                if edge_key not in metrics["affected_edges"]:
                    metrics["affected_edges"].append(edge_key)

                # Group matches by severity — emit one finding per severity level
                by_severity: dict[Severity, list[dict[str, Any]]] = {}
                for match in matches:
                    by_severity.setdefault(match["severity"], []).append(match)

                for sev, sev_matches in by_severity.items():
                    types_str = ", ".join(m["pattern_type"] for m in sev_matches)
                    for m in sev_matches:
                        if m["pattern_type"] not in metrics["pattern_types_found"]:
                            metrics["pattern_types_found"].append(m["pattern_type"])

                    findings.append(
                        Finding(
                            test_name=self.name,
                            severity=sev,
                            title=f"Sensitive data leaked ({types_str}): {src_name} → {dst_name}",
                            description=(
                                f"Event {event.id} ({event.event_type.value}) "
                                f"from '{src_name}' to '{dst_name}' contains "
                                f"{len(sev_matches)} sensitive pattern(s): {types_str}."
                            ),
                            affected_agents=[event.source_agent_id, event.target_agent_id],
                            evidence={
                                "event_id": event.id,
                                "event_type": event.event_type.value,
                                "patterns": [
                                    {
                                        "type": m["pattern_type"],
                                        "category": m["category"],
                                    }
                                    for m in sev_matches
                                ],
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
    def _scan_event(event: Any) -> list[dict[str, Any]]:
        """Return list of pattern match dicts found in the event."""
        text_parts = []

        for key, value in event.payload.items():
            text_parts.append(f"{key}={value!r}")

        if event.error_message:
            text_parts.append(event.error_message)

        full_text = " ".join(text_parts)
        return scan_text(full_text)

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
                            title=(
                                f"Restricted key '{key}' in agent context: "
                                f"{src_name} → {dst_name}"
                            ),
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
