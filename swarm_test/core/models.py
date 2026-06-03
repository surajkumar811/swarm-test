"""Core data models for swarm-test."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

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
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

    model_config = ConfigDict(arbitrary_types_allowed=True)


class InteractionEvent(BaseModel):
    """Records a single interaction between agents."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_agent_id: str
    target_agent_id: str
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float | None = None
    success: bool = True
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """A security or reliability finding from a test."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    test_name: str
    severity: Severity
    title: str
    description: str
    affected_agents: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
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
    findings: list[Finding] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    @property
    def passed(self) -> bool:
        return self.status == TestStatus.PASSED

    @property
    def critical_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.CRITICAL]

    @property
    def high_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.HIGH]

    def severity_count(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in Severity}
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
    test_results: list[TestResult] = Field(default_factory=list)
    graph_metrics: dict[str, Any] = Field(default_factory=dict)
    agent_scores: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    @property
    def all_findings(self) -> list[Finding]:
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

    def print_graph(self, *, graph: Any = None) -> None:
        """Print ASCII agent interaction graph to console."""
        from swarm_test.reporters.ascii_graph import AsciiGraphRenderer

        if graph is None:
            return
        renderer = AsciiGraphRenderer()
        renderer.render(graph, agent_scores=self.agent_scores)

    def to_markdown(self, output_path: str = "swarm_report.md") -> str:
        """Export report as Markdown."""
        from swarm_test.reporters.markdown import MarkdownReporter

        reporter = MarkdownReporter()
        return reporter.render(self, output_path)

    def to_html(self, output_path: str = "swarm_report.html") -> str:
        """Export report as HTML with D3 graph."""
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        return reporter.render(self, output_path)

    def to_json(
        self,
        output_path: str | None = None,
        *,
        graph: Any = None,
    ) -> dict[str, Any]:
        """Export report as structured JSON with enriched finding records.

        Each finding includes a stable ``finding_id`` hash, agent metadata
        resolved from the graph, risk_type classification, and blast_radius.

        Args:
            output_path: If provided, write JSON to this file path.
            graph: Optional ``SwarmGraph`` for resolving agent names/roles.

        Returns:
            The full JSON-serialisable dict.
        """
        # Build agent lookup from graph if available
        agent_lookup: dict[str, dict[str, str]] = {}
        if graph is not None:
            for nid, data in graph.graph.nodes(data=True):
                agent_lookup[nid] = {
                    "name": data.get("name", nid),
                    "role": data.get("role", "unknown"),
                }

        # Map test_name → risk_type
        risk_type_map = {
            "cascade_failure": "cascade",
            "context_leakage": "leakage",
            "collusion_detection": "collusion",
            "intent_drift": "drift",
            "timeout_resilience": "timeout",
            "blast_radius": "blast_radius",
        }

        enriched_findings: list[dict[str, Any]] = []
        for finding in self.all_findings:
            # Stable hash from swarm_name + test_name + title + affected_agents
            hash_input = (
                f"{self.swarm_name}:{finding.test_name}:"
                f"{finding.title}:{sorted(finding.affected_agents)}"
            )
            finding_id = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

            # Resolve primary agent (first in affected_agents)
            agent_id = finding.affected_agents[0] if finding.affected_agents else ""
            agent_info = agent_lookup.get(agent_id, {"name": agent_id, "role": "unknown"})

            # Resolve target agent (second in affected_agents, for edge findings)
            target_id = ""
            target_info: dict[str, str] = {"name": "", "role": ""}
            if len(finding.affected_agents) > 1:
                target_id = finding.affected_agents[1]
                target_info = agent_lookup.get(target_id, {"name": target_id, "role": "unknown"})

            # Edge key for edge-type findings
            edge_key = ""
            if agent_info["role"] and target_info["role"] and target_id:
                edge_key = f"{agent_info['role']} → {target_info['role']}"

            # Tool name from evidence if present
            tool_name = finding.evidence.get("tool_name", "")

            # Blast radius from evidence
            blast_radius = 0.0
            if "impact_percentage" in finding.evidence:
                blast_radius = round(finding.evidence["impact_percentage"] / 100.0, 4)
            elif "blast_radius" in finding.evidence:
                blast_radius = round(float(finding.evidence["blast_radius"]), 4)

            enriched_findings.append(
                {
                    "finding_id": finding_id,
                    "agent_id": agent_id,
                    "agent_name": agent_info["name"],
                    "agent_role": agent_info["role"],
                    "target_agent_id": target_id,
                    "target_agent_name": target_info["name"],
                    "target_agent_role": target_info["role"],
                    "tool_name": tool_name,
                    "edge_key": edge_key,
                    "risk_type": risk_type_map.get(finding.test_name, finding.test_name),
                    "severity": finding.severity.value,
                    "blast_radius": blast_radius,
                    "description": finding.description,
                    "remediation": finding.remediation,
                }
            )

        # Serialize agent health scores (detailed)
        agent_scores_json: list[dict[str, Any]] = []
        # Compact agent_health array for external integrations
        agent_health_json: list[dict[str, Any]] = []
        for aid, score_obj in self.agent_scores.items():
            agent_scores_json.append(
                {
                    "agent_id": score_obj.agent_id,
                    "agent_name": score_obj.agent_name,
                    "role": score_obj.role,
                    "score": score_obj.score,
                    "reasons": score_obj.reasons,
                    "breakdown": score_obj.breakdown,
                }
            )
            agent_health_json.append(
                {
                    "agent_id": score_obj.agent_id,
                    "agent_name": score_obj.agent_name,
                    "agent_role": score_obj.role,
                    "score": score_obj.score,
                    "source": "swarm-test",
                }
            )

        result = {
            "version": "0.2.2",
            "swarm_name": self.swarm_name,
            "framework": self.framework,
            "agent_count": self.agent_count,
            "edge_count": self.edge_count,
            "risk_score": self.risk_score,
            "total_findings": len(enriched_findings),
            "severity_summary": {
                "critical": sum(1 for f in enriched_findings if f["severity"] == "critical"),
                "high": sum(1 for f in enriched_findings if f["severity"] == "high"),
                "medium": sum(1 for f in enriched_findings if f["severity"] == "medium"),
                "low": sum(1 for f in enriched_findings if f["severity"] == "low"),
                "info": sum(1 for f in enriched_findings if f["severity"] == "info"),
            },
            "agent_health_scores": agent_scores_json,
            "agent_health": agent_health_json,
            "test_results": [
                {
                    "test_name": r.test_name,
                    "status": r.status.value,
                    "findings_count": len(r.findings),
                    "duration_ms": round(r.duration_ms, 2),
                }
                for r in self.test_results
            ],
            "findings": enriched_findings,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        if output_path:
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2, default=str)

        return result
