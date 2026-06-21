"""Rich console reporter for SwarmReport.

The reporter supports three verbosity levels:

- ``quiet``    : a single headline verdict line, nothing else.
- ``normal``   : headline + test results + CRITICAL/HIGH findings + SPOFs.
- ``verbose``  : headline + everything (LOW/INFO findings, graph metrics,
                 healthy agents, full redundancy table).
"""

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

# Severity → color used for the headline verdict line by certification level.
_LEVEL_STYLE = {
    "EXCELLENT": "bold bright_green",
    "GOOD": "green",
    "NEEDS IMPROVEMENT": "yellow",
    "AT RISK": "red",
    "CRITICAL": "bold bright_red",
}

_VALID_VERBOSITY = {"quiet", "normal", "verbose"}


_ROLE_COLORS = {
    "ORCHESTRATOR": "bold cyan",
    "AGGREGATOR": "cyan",
    "GATEWAY": "cyan",
    "VALIDATOR": "yellow",
    "ROUTER": "white",
    "WORKER": "white",
    "MONITOR": "dim",
    "UNKNOWN": "dim",
}


def _normalise_verbosity(v: str | None) -> str:
    if v is None:
        return "normal"
    vv = v.lower()
    return vv if vv in _VALID_VERBOSITY else "normal"


def _headline_text(report: SwarmReport) -> Text:
    """Build the single-line headline verdict text."""
    counts = report.severity_counts()
    n_critical = counts.get("critical", 0)
    n_high = counts.get("high", 0)
    score = report.swarm_score
    level = report.certification_level
    style = _LEVEL_STYLE.get(level, "white")

    findings_part: str
    if not report.all_findings:
        findings_part = "no findings"
    else:
        parts = []
        if n_critical:
            parts.append(f"{n_critical} critical")
        if n_high:
            parts.append(f"{n_high} high")
        if not parts:
            # No critical/high but other severities exist
            total_other = sum(counts.values())
            parts.append(f"{total_other} finding{'s' if total_other != 1 else ''}")
        findings_part = ", ".join(parts) + " findings"

    return Text.from_markup(f"[{style}]Swarm Score: {score}/100 — {level}[/] ({findings_part})")


class ConsoleReporter:
    """Renders a SwarmReport to the terminal using Rich."""

    def __init__(self, console: Any = None) -> None:
        self.console = console or Console(highlight=False)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def render(self, report: SwarmReport, *, verbosity: str = "normal") -> None:
        verbosity = _normalise_verbosity(verbosity)
        c = self.console

        # Headline verdict is always the first line.
        c.print(_headline_text(report))

        if verbosity == "quiet":
            return

        self._render_trend(report)
        self._render_body(report, verbosity)

    # ------------------------------------------------------------------
    # Trend (historical tracking)
    # ------------------------------------------------------------------

    def _render_trend(self, report: SwarmReport) -> None:
        """Render the trend line + new/resolved badges below the headline."""
        comparison = getattr(report, "comparison", None) or {}
        if not comparison:
            return
        c = self.console
        if comparison.get("first_run"):
            c.print(
                "[dim]First run — no history to compare yet. " "Future runs will show trends.[/dim]"
            )
            return

        delta = int(comparison.get("swarm_score_delta", 0))
        previous = int(comparison.get("previous_score", 0))
        current = int(comparison.get("current_score", previous + delta))
        if delta == 0:
            c.print(f"[dim]Trend: → no change ({current}/100, same as last run)[/dim]")
        elif delta > 0:
            arrow, style = "↑", "green"
            c.print(
                f"[{style}]Trend: {arrow} +{delta} " f"({current}/100, was {previous})[/{style}]"
            )
        else:
            arrow, style = "↓", "red"
            c.print(
                f"[{style}]Trend: {arrow} {delta} " f"({current}/100, was {previous})[/{style}]"
            )

        recent_scores = comparison.get("recent_scores") or []
        if len(recent_scores) >= 2:
            c.print("[dim]Recent: " + " → ".join(str(s) for s in recent_scores) + "[/dim]")

        new_count = len(comparison.get("new_findings") or [])
        resolved_count = len(comparison.get("resolved_findings") or [])
        regressed_count = len(comparison.get("regressed") or [])
        if resolved_count:
            plural = "s" if resolved_count != 1 else ""
            c.print(f"[green]✓ {resolved_count} finding{plural} resolved since last run[/green]")
        if new_count:
            plural = "s" if new_count != 1 else ""
            c.print(f"[yellow]⚠ {new_count} new finding{plural} since last run[/yellow]")
        if regressed_count:
            plural = "s" if regressed_count != 1 else ""
            c.print(f"[red]↑ {regressed_count} finding{plural} regressed in severity[/red]")

    # ------------------------------------------------------------------
    # Body rendering
    # ------------------------------------------------------------------

    def _render_agent_roles(self, report: SwarmReport, *, verbose: bool) -> None:
        """Render the inferred agent role taxonomy table."""
        from swarm_test.core.taxonomy import RISK_PROFILES

        c = self.console
        rows: list[tuple[str, str, float, dict[str, Any]]] = []
        for aid, info in report.agent_roles.items():
            role = info.get("role", "UNKNOWN")
            conf = float(info.get("confidence", 0.0))
            score_obj = report.agent_scores.get(aid)
            name = score_obj.agent_name if score_obj is not None else aid
            profile = RISK_PROFILES.get(role, {})
            rows.append((name, role, conf, profile))
        if not rows:
            return

        rows.sort(key=lambda r: (-r[2], r[0]))

        c.print(Rule("[bold cyan]Agent Roles[/bold cyan]"))
        c.print()
        roles_table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        roles_table.add_column("Agent", style="bold", width=28)
        roles_table.add_column("Role", width=14)
        roles_table.add_column("Confidence", width=12, justify="center")
        roles_table.add_column("Profile", min_width=30)

        for name, role, conf, profile in rows:
            role_style = _ROLE_COLORS.get(role, "white")
            tags: list[str] = []
            if profile.get("critical"):
                tags.append("critical")
            if profile.get("needs_fallback"):
                tags.append("needs fallback")
            if profile.get("security_sensitive"):
                tags.append("security-sensitive")
            if profile.get("expected_high_blast_radius"):
                tags.append("expected high blast")
            profile_str = ", ".join(tags) if tags else "standard"
            roles_table.add_row(
                name,
                Text(role, style=role_style),
                f"{int(round(conf * 100))}%",
                Text(profile_str, style="dim"),
            )
        c.print(roles_table)
        c.print()

    def _render_body(self, report: SwarmReport, verbosity: str) -> None:
        c = self.console
        verbose = verbosity == "verbose"

        c.print()
        c.print(Rule("[bold blue]SWARM-TEST RELIABILITY REPORT[/bold blue]"))
        c.print()

        # Header panel — single consistent metric: Swarm Score (higher = healthier).
        swarm_score_val = report.swarm_score
        score_color = (
            "green" if swarm_score_val >= 75 else "yellow" if swarm_score_val >= 50 else "red"
        )
        header_text = (
            f"[bold]Swarm:[/bold] {report.swarm_name}\n"
            f"[bold]Framework:[/bold] {report.framework}\n"
            f"[bold]Agents:[/bold] {report.agent_count}   "
            f"[bold]Edges:[/bold] {report.edge_count}\n"
            f"[bold]Swarm Score:[/bold] [{score_color}]{swarm_score_val}/100"
            f" — {report.certification_level}[/{score_color}]\n"
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

        # Graph metrics — verbose only
        if verbose and report.graph_metrics:
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

        # Agent roles — taxonomy classification
        if report.agent_roles:
            self._render_agent_roles(report, verbose=verbose)

        # Agent health scores — verbose shows all, normal hides healthy
        if report.agent_scores:
            sorted_scores = sorted(report.agent_scores.values(), key=lambda s: s.score)
            if not verbose:
                sorted_scores = [hs for hs in sorted_scores if hs.score < 70]
            if sorted_scores:
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

        # Agent redundancy: verbose shows full table, normal shows SPOFs only
        if report.redundancy_scores:
            rows = []
            for agent_id, score in report.redundancy_scores.items():
                score_obj = report.agent_scores.get(agent_id)
                name = score_obj.agent_name if score_obj is not None else agent_id
                rows.append((name, float(score)))
            rows.sort(key=lambda r: r[1])

            if not verbose:
                rows = [r for r in rows if r[1] < 20]

            if rows:
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

        # Findings — verbose shows all, normal filters to CRITICAL + HIGH
        all_findings = report.all_findings
        if not all_findings:
            c.print(
                Panel(
                    "[green]No findings — all tests passed cleanly.[/green]", border_style="green"
                )
            )
        else:
            severity_order = [
                Severity.CRITICAL,
                Severity.HIGH,
                Severity.MEDIUM,
                Severity.LOW,
                Severity.INFO,
            ]
            visible = sorted(all_findings, key=lambda f: severity_order.index(f.severity))
            if not verbose:
                visible = [f for f in visible if f.severity in (Severity.CRITICAL, Severity.HIGH)]

            hidden_count = len(all_findings) - len(visible)
            if visible:
                c.print(
                    Rule(
                        f"[bold yellow]Findings ({len(visible)} shown / "
                        f"{len(all_findings)} total)[/bold yellow]"
                    )
                )
                c.print()
                for finding in visible:
                    color = _SEVERITY_COLORS.get(finding.severity, "white")
                    badge = _SEVERITY_EMOJI.get(finding.severity, "[ ]")
                    title = (
                        f"{badge} [{color}]{finding.severity.value.upper()}[/{color}] | "
                        f"{finding.test_name}"
                    )
                    arrow_line = ""
                    if finding.remediation:
                        arrow_line = f"\n\n[bold cyan]→[/bold cyan] {finding.remediation}"
                    content = (
                        f"[bold]{finding.title}[/bold]\n\n" f"{finding.description}" f"{arrow_line}"
                    )
                    c.print(Panel(content, title=title, border_style=color.split()[-1]))
                    c.print()
            if hidden_count:
                c.print(
                    f"[dim]({hidden_count} lower-severity finding"
                    f"{'s' if hidden_count != 1 else ''} hidden — re-run with "
                    f"--verbose to see them.)[/dim]"
                )
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
