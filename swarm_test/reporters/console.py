"""Rich console reporter for SwarmReport."""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from swarm_test.core.models import Severity, SwarmReport, TestStatus, redundancy_level

_SEVERITY_COLORS = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}

_STATUS_COLORS = {
    TestStatus.PASSED: "green",
    TestStatus.FAILED: "red",
    TestStatus.SKIPPED: "yellow",
    TestStatus.ERROR: "bold red",
}

_SEVERITY_EMOJI = {
    Severity.CRITICAL: "[X]",
    Severity.HIGH: "[!]",
    Severity.MEDIUM: "[~]",
    Severity.LOW: "[-]",
    Severity.INFO: "[i]",
}


class ConsoleReporter:
    """Renders a SwarmReport to the terminal using Rich."""

    def __init__(self, console: Any = None) -> None:
        self.console = console or Console(highlight=False)

    def render(self, report: SwarmReport) -> None:
        c = self.console
        c.print()
        c.print(Rule("[bold blue]SWARM-TEST RELIABILITY REPORT[/bold blue]"))
        c.print()

        # Header panel
        risk_color = (
            "red" if report.risk_score >= 60 else "yellow" if report.risk_score >= 30 else "green"
        )
        header_text = (
            f"[bold]Swarm:[/bold] {report.swarm_name}\n"
            f"[bold]Framework:[/bold] {report.framework}\n"
            f"[bold]Agents:[/bold] {report.agent_count}   "
            f"[bold]Edges:[/bold] {report.edge_count}\n"
            f"[bold]Risk Score:[/bold] [{risk_color}]{report.risk_score:.0f}/100[/{risk_color}]\n"
            f"[bold]Duration:[/bold] {report.total_duration_ms:.0f}ms"
        )
        c.print(Panel(header_text, title="[bold]Summary[/bold]", border_style="blue"))
        c.print()

        # Test results table
        table = Table(
            title="Test Results",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Test", style="bold", width=24)
        table.add_column("Status", width=10)
        table.add_column("Findings", width=12, justify="center")
        table.add_column("Critical", width=10, justify="center")
        table.add_column("High", width=8, justify="center")
        table.add_column("Duration", width=12, justify="right")

        for result in report.test_results:
            status_color = _STATUS_COLORS.get(result.status, "white")
            status_text = Text(result.status.value.upper(), style=status_color)
            sev = result.severity_count()
            table.add_row(
                result.test_name,
                status_text,
                str(len(result.findings)),
                Text(
                    str(sev.get("critical", 0)),
                    style="bold red" if sev.get("critical", 0) else "dim",
                ),
                Text(str(sev.get("high", 0)), style="red" if sev.get("high", 0) else "dim"),
                f"{result.duration_ms:.1f}ms",
            )

        c.print(table)
        c.print()

        # Graph metrics
        if report.graph_metrics:
            gm = report.graph_metrics
            gm_text = (
                f"[bold]Nodes:[/bold] {gm.get('node_count', '?')}   "
                f"[bold]Edges:[/bold] {gm.get('edge_count', '?')}   "
                f"[bold]Density:[/bold] {gm.get('density', 0):.4f}   "
                f"[bold]Cycles:[/bold] {gm.get('cycle_count', 0)}   "
                f"[bold]SPOFs:[/bold] {gm.get('single_points_of_failure', 0)}   "
                f"[bold]Critical Path:[/bold] {gm.get('critical_path_length', 0)} hops"
            )
            c.print(Panel(gm_text, title="[bold]Graph Metrics[/bold]", border_style="cyan"))
            c.print()

        # Agent health scores
        if report.agent_scores:
            c.print(Rule("[bold cyan]Agent Health Scores[/bold cyan]"))
            c.print()
            health_table = Table(
                box=box.ROUNDED,
                show_header=True,
                header_style="bold cyan",
            )
            health_table.add_column("Agent", style="bold", width=28)
            health_table.add_column("Score", width=12, justify="center")
            health_table.add_column("Status", width=10, justify="center")
            health_table.add_column("Details", min_width=40)

            # Sort worst to best
            sorted_scores = sorted(report.agent_scores.values(), key=lambda s: s.score)
            for hs in sorted_scores:
                if hs.score >= 70:
                    score_style = "green"
                elif hs.score >= 40:
                    score_style = "yellow"
                else:
                    score_style = "bold red"
                reasons_str = ", ".join(hs.reasons) if hs.reasons else "no issues"
                health_table.add_row(
                    hs.agent_name,
                    Text(f"{hs.score}/100", style=score_style),
                    Text(hs.status_icon, justify="center"),
                    Text(f"({reasons_str})", style="dim"),
                )
            c.print(health_table)
            c.print()

        # Agent redundancy scores
        if report.redundancy_scores:
            c.print(Rule("[bold cyan]Agent Redundancy[/bold cyan]"))
            c.print()
            redundancy_table = Table(
                box=box.ROUNDED,
                show_header=True,
                header_style="bold cyan",
            )
            redundancy_table.add_column("Agent", style="bold", width=28)
            redundancy_table.add_column("Score", width=12, justify="center")
            redundancy_table.add_column("Level", width=18, justify="center")
            redundancy_table.add_column("Risk", width=12, justify="center")

            # Build (agent_id, name, score) tuples sorted worst → best
            rows = []
            for agent_id, score in report.redundancy_scores.items():
                score_obj = report.agent_scores.get(agent_id)
                name = score_obj.agent_name if score_obj is not None else agent_id
                rows.append((name, float(score)))
            rows.sort(key=lambda r: r[1])

            for name, score in rows:
                level = redundancy_level(score)
                if score <= 20:
                    score_style = "bold red"
                elif score <= 40:
                    score_style = "yellow"
                elif score <= 60:
                    score_style = "white"
                elif score <= 80:
                    score_style = "green"
                else:
                    score_style = "bold bright_green"
                risk_label = "SPOF" if score < 20 else ("Monitor" if score <= 60 else "Safe")
                risk_style = (
                    "bold red"
                    if risk_label == "SPOF"
                    else "yellow" if risk_label == "Monitor" else "green"
                )
                redundancy_table.add_row(
                    name,
                    Text(f"{score:.0f}/100", style=score_style),
                    Text(level, style=score_style),
                    Text(risk_label, style=risk_style),
                )
            c.print(redundancy_table)
            c.print()

        # Findings detail
        all_findings = report.all_findings
        if not all_findings:
            c.print(
                Panel(
                    "[green]No findings — all tests passed cleanly.[/green]", border_style="green"
                )
            )
        else:
            c.print(Rule(f"[bold yellow]Findings ({len(all_findings)} total)[/bold yellow]"))
            c.print()

            # Sort by severity
            severity_order = [
                Severity.CRITICAL,
                Severity.HIGH,
                Severity.MEDIUM,
                Severity.LOW,
                Severity.INFO,
            ]
            sorted_findings = sorted(
                all_findings,
                key=lambda f: severity_order.index(f.severity),
            )

            for finding in sorted_findings:
                color = _SEVERITY_COLORS.get(finding.severity, "white")
                badge = _SEVERITY_EMOJI.get(finding.severity, "[ ]")
                title = f"{badge} [{color}]{finding.severity.value.upper()}[/{color}] | {finding.test_name}"
                content = (
                    f"[bold]{finding.title}[/bold]\n\n"
                    f"{finding.description}\n\n"
                    f"[bold]Remediation:[/bold] {finding.remediation}"
                )
                c.print(Panel(content, title=title, border_style=color.split()[-1]))
                c.print()

        # Footer
        passed_icon = "[green]PASSED[/green]" if report.failed_count == 0 else "[red]FAILED[/red]"
        c.print(
            Rule(
                f"[bold]{report.passed_count}/{len(report.test_results)} tests passed | "
                f"Overall: {passed_icon}[/bold]"
            )
        )
        c.print()
