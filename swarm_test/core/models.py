"""Core data models for swarm-test."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class EventType(str, Enum):
    AGENT_CALL = "agent_call"
    AGENT_RESPONSE = "agent_response"
    TOOL_USE = "tool_use"
    CONTEXT_SHARE = "context_share"
    ERROR = "error"
    TIMEOUT = "timeout"
    TASK_DELEGATE = "task_delegate"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"


class TestStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class AgentNode(BaseModel):
    """Represents an agent in the swarm graph."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    role: str = "unknown"
    framework: str = "unknown"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

    model_config = ConfigDict(arbitrary_types_allowed=True)


class InteractionEvent(BaseModel):
    """Records a single interaction between agents."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_agent_id: str
    target_agent_id: str
    event_type: EventType
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: Optional[float] = None
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """A security or reliability finding from a test."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    test_name: str
    severity: Severity
    title: str
    description: str
    affected_agents: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    remediation: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "test_name": self.test_name,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "affected_agents": self.affected_agents,
            "evidence": self.evidence,
            "remediation": self.remediation,
            "timestamp": self.timestamp.isoformat(),
        }


class TestResult(BaseModel):
    """Result of a single chaos test."""

    test_name: str
    status: TestStatus
    duration_ms: float = 0.0
    findings: List[Finding] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @property
    def passed(self) -> bool:
        return self.status == TestStatus.PASSED

    @property
    def critical_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == Severity.CRITICAL]

    @property
    def high_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == Severity.HIGH]

    def severity_count(self) -> Dict[str, int]:
        counts: Dict[str, int] = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts


class SwarmReport(BaseModel):
    """Aggregated report for all swarm tests."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    swarm_name: str = "unnamed-swarm"
    framework: str = "unknown"
    agent_count: int = 0
    edge_count: int = 0
    test_results: List[TestResult] = Field(default_factory=list)
    graph_metrics: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @property
    def all_findings(self) -> List[Finding]:
        findings = []
        for result in self.test_results:
            findings.extend(result.findings)
        return findings

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.test_results if r.status == TestStatus.PASSED)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.test_results if r.status == TestStatus.FAILED)

    @property
    def total_duration_ms(self) -> float:
        return sum(r.duration_ms for r in self.test_results)

    @property
    def risk_score(self) -> float:
        """0-100 risk score based on findings severity."""
        weights = {
            Severity.CRITICAL: 40,
            Severity.HIGH: 20,
            Severity.MEDIUM: 10,
            Severity.LOW: 5,
            Severity.INFO: 1,
        }
        total = sum(weights.get(f.severity, 0) for f in self.all_findings)
        return min(100.0, float(total))

    def print_summary(self) -> None:
        """Print a rich-formatted summary to console."""
        from swarm_test.reporters.console import ConsoleReporter

        reporter = ConsoleReporter()
        reporter.render(self)

    def to_html(self, output_path: str = "swarm_report.html") -> str:
        """Export report as HTML with D3 graph."""
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        return reporter.render(self, output_path)
