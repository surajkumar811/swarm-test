"""Rich console reporter for SwarmReport."""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from swarm_test.core.models import Severity, SwarmReport, TestStatus

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
            "red" if report.risk_score >= 60
            else "yellow" if report.risk_score >= 30
            else "green"
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
                Text(str(sev.get("critical", 0)), style="bold red" if sev.get("critical", 0) else "dim"),
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

        # Findings detail
        all_findings = report.all_findings
        if not all_findings:
            c.print(Panel("[green]No findings — all tests passed cleanly.[/green]", border_style="green"))
        else:
            c.print(Rule(f"[bold yellow]Findings ({len(all_findings)} total)[/bold yellow]"))
            c.print()

            # Sort by severity
            severity_order = [
                Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO
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
