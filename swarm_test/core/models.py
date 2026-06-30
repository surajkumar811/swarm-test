"""Core data models for swarm-test."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Strip numeric values from finding titles before hashing so small topology
# changes (e.g. a cascade count going from 12 → 13 agents) don't churn the
# finding_id. Mirrors swarm_test.history._normalize_title.
_TITLE_NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?%?")


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


def redundancy_level(score: float) -> str:
    """Map a 0-100 redundancy score to a human-readable level."""
    if score <= 20:
        return "IRREPLACEABLE"
    if score <= 40:
        return "LOW"
    if score <= 60:
        return "MODERATE"
    if score <= 80:
        return "HIGH"
    return "FULLY REDUNDANT"


class AgentNode(BaseModel):
    """Represents an agent in the swarm graph."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    role: str = "unknown"
    framework: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    classified_role: str = "UNKNOWN"
    role_confidence: float = 0.0
    # User-declared intentional role — bypasses structural inference. Set to
    # "ORCHESTRATOR" to mark an agent as the intentional central hub so the
    # attacks treat its high blast radius / centrality as by-design instead of
    # firing CRITICAL findings on every spoke.
    intentional_role: str | None = None

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
    redundancy_scores: dict[str, float] = Field(default_factory=dict)
    agent_roles: dict[str, dict[str, Any]] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    comparison: dict[str, Any] | None = Field(
        default=None,
        description="Trend comparison vs the most recent prior run, if history is enabled.",
    )

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

    @property
    def swarm_score(self) -> int:
        """0-100 swarm reliability score (100 = best).

        Computed from sub-scores rather than a hard inverse of ``risk_score``
        so that a less-risky or more-redundant topology produces a higher
        score even when raw finding counts are similar. Penalty weights are
        deliberately lighter than ``risk_score`` so the score does not
        saturate at 0 after a handful of CRITICAL findings — that loses the
        ability to track topology improvements run-over-run.
        """
        penalty_weights = {
            Severity.CRITICAL: 15.0,
            Severity.HIGH: 8.0,
            Severity.MEDIUM: 3.0,
            Severity.LOW: 1.0,
            # INFO findings are documentation of intentional design decisions
            # (e.g. "this is the recognised hub") — they belong in the report
            # for context, not as a score penalty.
            Severity.INFO: 0.1,
        }
        findings_penalty = sum(penalty_weights.get(f.severity, 0.0) for f in self.all_findings)

        # Topology penalty: low average redundancy → up to -25 pts. A 2-hub
        # redundant topology (high redundancy) keeps this term near 0, while
        # a hub-spoke with 5 workers (low redundancy / SPOFs) loses points.
        # When the user has declared an intentional hub (confidence == 1.0
        # on a hub role), we halve the topology penalty: hub-and-spoke has
        # inherently low redundancy and penalising the user for the design
        # they explicitly chose is double-counting their own decision.
        topology_penalty = 0.0
        if self.redundancy_scores:
            avg_redundancy = sum(float(r) for r in self.redundancy_scores.values()) / len(
                self.redundancy_scores
            )
            topology_penalty = max(0.0, 100.0 - avg_redundancy) * 0.25
            if self._has_declared_intentional_hub():
                topology_penalty *= 0.5

        score = 100.0 - findings_penalty - topology_penalty
        return int(round(max(0.0, min(100.0, score))))

    def _has_declared_intentional_hub(self) -> bool:
        """True when at least one agent has a user-declared intentional hub role.

        Declared roles come back from ``classify_agent`` with confidence
        exactly 1.0 and a hub role (ORCHESTRATOR or AGGREGATOR). We treat
        this as the user accepting hub-and-spoke as their design.
        """
        hub_roles = {"ORCHESTRATOR", "AGGREGATOR"}
        for info in self.agent_roles.values():
            try:
                conf = float(info.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if conf >= 0.999 and info.get("role") in hub_roles:
                return True
        return False

    @property
    def certification_level(self) -> str:
        """Categorical label derived from the swarm_score."""
        s = self.swarm_score
        if s >= 90:
            return "EXCELLENT"
        if s >= 75:
            return "GOOD"
        if s >= 50:
            return "NEEDS IMPROVEMENT"
        if s >= 25:
            return "AT RISK"
        return "CRITICAL"

    @property
    def cost_risk_score(self) -> int | None:
        """0-100 cost-waste risk score from the cost_risk attack, if it ran."""
        for r in self.test_results:
            if r.test_name == "cost_risk":
                val = r.metrics.get("cost_risk_score")
                if val is None:
                    return None
                return int(val)
        return None

    @property
    def cost_risk_verdict(self) -> str | None:
        """LOW / MODERATE / HIGH / SEVERE verdict from the cost_risk attack."""
        for r in self.test_results:
            if r.test_name == "cost_risk":
                val = r.metrics.get("cost_risk_verdict")
                return str(val) if val is not None else None
        return None

    @property
    def cost_risk_drivers(self) -> list[str]:
        """Human-readable list of dominant cost-risk drivers (empty if none)."""
        for r in self.test_results:
            if r.test_name == "cost_risk":
                drivers = r.metrics.get("cost_risk_drivers") or []
                return [str(d) for d in drivers]
        return []

    def severity_counts(self) -> dict[str, int]:
        """Return a dict of severity → count across all findings."""
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for f in self.all_findings:
            counts[f.severity.value] += 1
        return counts

    def print_summary(self, verbosity: str = "normal") -> None:
        """Print a rich-formatted summary to console.

        Args:
            verbosity: "quiet" (headline only), "normal" (default),
                       or "verbose" (every finding + every detail).
        """
        from swarm_test.reporters.console import ConsoleReporter

        reporter = ConsoleReporter()
        reporter.render(self, verbosity=verbosity)

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
            "trajectory_analysis": "trajectory",
            "cost_risk": "cost_risk",
        }

        enriched_findings: list[dict[str, Any]] = []
        for finding in self.all_findings:
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

            # Stable hash from swarm_name + test_name + normalised title +
            # primary agent NAME. Agent UUIDs regenerate every run; names
            # ("Hub", "W1") are stable. Numeric values in the title are
            # stripped so small count drift ("12 agents" → "13 agents")
            # doesn't churn the ID, and only the primary agent enters the
            # hash so the non-deterministic order of an SPOF's downstream
            # list doesn't shift its identity. The normalised title already
            # carries the target name for edge findings. Never fold UUID,
            # blast-radius %, or timestamp into this hash.
            primary_name = agent_info["name"] if agent_id else ""
            title_template = _TITLE_NUMERIC_RE.sub("N", finding.title or "")
            hash_input = (
                f"{self.swarm_name}:{finding.test_name}:" f"{title_template}:{primary_name}"
            )
            finding_id = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

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
            r_score = getattr(score_obj, "redundancy_score", 0.0)
            agent_scores_json.append(
                {
                    "agent_id": score_obj.agent_id,
                    "agent_name": score_obj.agent_name,
                    "role": score_obj.role,
                    "score": score_obj.score,
                    "reasons": score_obj.reasons,
                    "breakdown": score_obj.breakdown,
                    "redundancy_score": r_score,
                    "redundancy_level": redundancy_level(r_score),
                }
            )
            agent_health_json.append(
                {
                    "agent_id": score_obj.agent_id,
                    "agent_name": score_obj.agent_name,
                    "agent_role": score_obj.role,
                    "score": score_obj.score,
                    "redundancy_score": r_score,
                    "source": "swarm-test",
                }
            )

        # Per-agent redundancy export
        intentional_hub_ids: set[str] = {
            aid
            for aid, info in self.agent_roles.items()
            if float(info.get("confidence", 0.0)) >= 0.999
            and str(info.get("role", "")).upper() in {"ORCHESTRATOR", "AGGREGATOR"}
        }
        redundancy_json: list[dict[str, Any]] = []
        for aid, r_score in self.redundancy_scores.items():
            score_obj = self.agent_scores.get(aid)
            name = score_obj.agent_name if score_obj else aid
            role = score_obj.role if score_obj else "unknown"
            redundancy_json.append(
                {
                    "agent_id": aid,
                    "agent_name": name,
                    "agent_role": role,
                    "score": round(float(r_score), 2),
                    "level": redundancy_level(float(r_score)),
                    "is_intentional_hub": aid in intentional_hub_ids,
                }
            )

        # Per-agent classified roles export
        from swarm_test.core.taxonomy import RISK_PROFILES

        agent_roles_json: list[dict[str, Any]] = []
        for aid, role_info in self.agent_roles.items():
            score_obj = self.agent_scores.get(aid)
            name = score_obj.agent_name if score_obj else aid
            classified = role_info.get("role", "UNKNOWN")
            agent_roles_json.append(
                {
                    "agent_id": aid,
                    "agent_name": name,
                    "role": classified,
                    "confidence": round(float(role_info.get("confidence", 0.0)), 2),
                    "profile": RISK_PROFILES.get(classified, {}),
                }
            )
        overall_redundancy = (
            round(
                sum(float(r) for r in self.redundancy_scores.values())
                / len(self.redundancy_scores),
                2,
            )
            if self.redundancy_scores
            else 0.0
        )

        comparison_block: dict[str, Any] | None = None
        if self.comparison:
            comparison_block = {
                "first_run": bool(self.comparison.get("first_run", False)),
                "trend": self.comparison.get("trend"),
                "swarm_score_delta": self.comparison.get("swarm_score_delta"),
                "previous_score": self.comparison.get("previous_score"),
                "current_score": self.comparison.get("current_score", self.swarm_score),
                "new_findings_count": len(self.comparison.get("new_findings") or []),
                "resolved_findings_count": len(self.comparison.get("resolved_findings") or []),
                "regressed_count": len(self.comparison.get("regressed") or []),
                "recent_scores": self.comparison.get("recent_scores") or [],
            }

        result = {
            "version": "0.2.2",
            "swarm_name": self.swarm_name,
            "framework": self.framework,
            "agent_count": self.agent_count,
            "edge_count": self.edge_count,
            "risk_score": self.risk_score,
            "cost_risk_score": self.cost_risk_score,
            "cost_risk_verdict": self.cost_risk_verdict,
            "cost_risk_drivers": self.cost_risk_drivers,
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
            "redundancy_scores": redundancy_json,
            "overall_redundancy": overall_redundancy,
            "agent_roles": agent_roles_json,
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
            "comparison": comparison_block,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        if output_path:
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2, default=str)

        return result
