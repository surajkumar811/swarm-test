"""Report comparison — diff two JSON reports to track reliability changes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text


class ChangeType(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    NEW = "new"
    RESOLVED = "resolved"
    UNCHANGED = "unchanged"


@dataclass
class MetricDelta:
    """A single metric comparison."""

    name: str
    before: Any
    after: Any
    change_type: ChangeType
    delta: str = ""


@dataclass
class ComparisonResult:
    """Full comparison between two reports."""

    before_name: str
    after_name: str
    metric_deltas: list[MetricDelta] = field(default_factory=list)
    new_findings: list[dict[str, Any]] = field(default_factory=list)
    resolved_findings: list[dict[str, Any]] = field(default_factory=list)
    agent_deltas: list[MetricDelta] = field(default_factory=list)

    @property
    def improved_count(self) -> int:
        all_deltas = self.metric_deltas + self.agent_deltas
        return sum(1 for d in all_deltas if d.change_type == ChangeType.IMPROVED)

    @property
    def regressed_count(self) -> int:
        all_deltas = self.metric_deltas + self.agent_deltas
        return sum(1 for d in all_deltas if d.change_type == ChangeType.REGRESSED)


class ReportComparator:
    """Compares two swarm-test JSON reports and produces a structured diff."""

    def compare(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> ComparisonResult:
        """Compare two report dicts (as returned by SwarmReport.to_json)."""
        result = ComparisonResult(
            before_name=before.get("swarm_name", "before"),
            after_name=after.get("swarm_name", "after"),
        )

        # Top-level metrics (lower is better)
        self._compare_metric(
            result,
            "Risk Score",
            before.get("risk_score", 0),
            after.get("risk_score", 0),
            lower_is_better=True,
        )
        self._compare_metric(
            result,
            "Total Findings",
            before.get("total_findings", 0),
            after.get("total_findings", 0),
            lower_is_better=True,
        )

        # Severity breakdown
        before_sev = before.get("severity_summary", {})
        after_sev = after.get("severity_summary", {})
        for level in ("critical", "high", "medium", "low"):
            self._compare_metric(
                result,
                level.capitalize(),
                before_sev.get(level, 0),
                after_sev.get(level, 0),
                lower_is_better=True,
            )

        # Per-test findings count
        before_tests = {t["test_name"]: t for t in before.get("test_results", [])}
        after_tests = {t["test_name"]: t for t in after.get("test_results", [])}
        for test_name in sorted(set(before_tests) | set(after_tests)):
            b_count = before_tests.get(test_name, {}).get("findings_count", 0)
            a_count = after_tests.get(test_name, {}).get("findings_count", 0)
            self._compare_metric(
                result,
                f"Test: {test_name}",
                b_count,
                a_count,
                lower_is_better=True,
            )

        # Agent health scores
        before_agents = {a["agent_name"]: a for a in before.get("agent_health_scores", [])}
        after_agents = {a["agent_name"]: a for a in after.get("agent_health_scores", [])}
        all_agent_names = sorted(set(before_agents) | set(after_agents))
        for name in all_agent_names:
            b_score = before_agents.get(name, {}).get("score")
            a_score = after_agents.get(name, {}).get("score")
            if b_score is not None and a_score is not None:
                delta = self._make_delta(
                    f"Agent: {name}",
                    b_score,
                    a_score,
                    lower_is_better=False,
                    format_fn=lambda v: f"{v}/100",
                )
                result.agent_deltas.append(delta)
            elif b_score is None and a_score is not None:
                result.agent_deltas.append(
                    MetricDelta(
                        name=f"Agent: {name}",
                        before="\u2014",
                        after=f"{a_score}/100",
                        change_type=ChangeType.NEW,
                        delta="NEW",
                    )
                )
            elif b_score is not None and a_score is None:
                result.agent_deltas.append(
                    MetricDelta(
                        name=f"Agent: {name}",
                        before=f"{b_score}/100",
                        after="\u2014",
                        change_type=ChangeType.RESOLVED,
                        delta="REMOVED",
                    )
                )

        # Finding-level diff by finding_id
        before_ids = {f["finding_id"]: f for f in before.get("findings", [])}
        after_ids = {f["finding_id"]: f for f in after.get("findings", [])}

        for fid, finding in after_ids.items():
            if fid not in before_ids:
                result.new_findings.append(finding)
        for fid, finding in before_ids.items():
            if fid not in after_ids:
                result.resolved_findings.append(finding)

        return result

    @staticmethod
    def _compare_metric(
        result: ComparisonResult,
        name: str,
        before_val: float | int,
        after_val: float | int,
        *,
        lower_is_better: bool = True,
    ) -> None:
        delta = ReportComparator._make_delta(
            name,
            before_val,
            after_val,
            lower_is_better=lower_is_better,
        )
        result.metric_deltas.append(delta)

    @staticmethod
    def _make_delta(
        name: str,
        before_val: Any,
        after_val: Any,
        *,
        lower_is_better: bool = True,
        format_fn: Any = None,
    ) -> MetricDelta:
        b = before_val if isinstance(before_val, (int, float)) else 0
        a = after_val if isinstance(after_val, (int, float)) else 0
        diff = a - b

        if diff == 0:
            change_type = ChangeType.UNCHANGED
            delta_str = "\u2014 No change"
        elif (diff < 0 and lower_is_better) or (diff > 0 and not lower_is_better):
            change_type = ChangeType.IMPROVED
            sign = "+" if diff > 0 else ""
            if lower_is_better and b != 0 and a == 0:
                delta_str = "Fixed"
            else:
                delta_str = f"{sign}{diff:g}"
        else:
            change_type = ChangeType.REGRESSED
            sign = "+" if diff > 0 else ""
            delta_str = f"{sign}{diff:g}"

        fmt = format_fn or (lambda v: str(v))
        return MetricDelta(
            name=name,
            before=fmt(before_val),
            after=fmt(after_val),
            change_type=change_type,
            delta=delta_str,
        )

    def print_comparison(
        self,
        result: ComparisonResult,
        console: Console | None = None,
    ) -> None:
        """Print a Rich comparison table to the console."""
        c = console or Console(highlight=False)

        c.print()
        c.print(
            f"[bold blue]swarm-test compare[/bold blue]  "
            f"[dim]{result.before_name}[/dim] vs [dim]{result.after_name}[/dim]"
        )
        c.print()

        # Main metrics table
        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
            title="Report Comparison",
        )
        table.add_column("Metric", style="bold", min_width=24)
        table.add_column("Before", width=12, justify="center")
        table.add_column("After", width=12, justify="center")
        table.add_column("Change", width=16, justify="center")

        for delta in result.metric_deltas:
            change_text = self._format_change(delta)
            table.add_row(delta.name, str(delta.before), str(delta.after), change_text)

        # Agent deltas
        for delta in result.agent_deltas:
            change_text = self._format_change(delta)
            table.add_row(delta.name, str(delta.before), str(delta.after), change_text)

        c.print(table)

        # New findings
        if result.new_findings:
            c.print()
            c.print(f"[bold yellow]New Findings ({len(result.new_findings)})[/bold yellow]")
            for f in result.new_findings:
                sev = f.get("severity", "?").upper()
                desc = f.get("description", "")[:80]
                c.print(f"  [yellow]\u26a0\ufe0f NEW[/yellow] [{sev}] {desc}")

        # Resolved findings
        if result.resolved_findings:
            c.print()
            c.print(f"[bold green]Resolved Findings ({len(result.resolved_findings)})[/bold green]")
            for f in result.resolved_findings:
                sev = f.get("severity", "?").upper()
                desc = f.get("description", "")[:80]
                c.print(f"  [green]\u2705 RESOLVED[/green] [{sev}] {desc}")

        # Summary
        c.print()
        improved = result.improved_count
        regressed = result.regressed_count
        if regressed == 0 and improved > 0:
            c.print(
                f"[bold green]Overall: {improved} metric(s) improved, no regressions[/bold green]"
            )
        elif regressed > 0:
            c.print(
                f"[bold red]Overall: {regressed} regression(s), "
                f"{improved} improvement(s)[/bold red]"
            )
        else:
            c.print("[bold]Overall: No significant changes[/bold]")
        c.print()

    @staticmethod
    def _format_change(delta: MetricDelta) -> Text:
        if delta.change_type == ChangeType.IMPROVED:
            return Text(f"\u2705 {delta.delta}", style="green")
        if delta.change_type == ChangeType.REGRESSED:
            return Text(f"\u274c {delta.delta}", style="red")
        if delta.change_type == ChangeType.NEW:
            return Text(f"\u26a0\ufe0f {delta.delta}", style="yellow")
        if delta.change_type == ChangeType.RESOLVED:
            return Text(f"\u2705 {delta.delta}", style="green")
        return Text(f"{delta.delta}", style="dim")


def load_report(path: str) -> dict[str, Any]:
    """Load a JSON report file."""
    with open(path) as f:
        return json.load(f)
