"""Markdown reporter for SwarmReport — GitHub-friendly .md output."""

from __future__ import annotations

from datetime import datetime, timezone

from swarm_test.core.models import Severity, SwarmReport, TestStatus

_SEVERITY_BADGE = {
    Severity.CRITICAL: "\U0001f534 **CRITICAL**",
    Severity.HIGH: "\U0001f7e0 HIGH",
    Severity.MEDIUM: "\U0001f7e1 MEDIUM",
    Severity.LOW: "\U0001f535 LOW",
    Severity.INFO: "\u2139\ufe0f INFO",
}

_STATUS_ICON = {
    TestStatus.PASSED: "\u2705",
    TestStatus.FAILED: "\u274c",
    TestStatus.SKIPPED: "\u23ed\ufe0f",
    TestStatus.ERROR: "\U0001f4a5",
}


class MarkdownReporter:
    """Generates a Markdown reliability report suitable for GitHub PRs."""

    def render(self, report: SwarmReport, output_path: str = "swarm_report.md") -> str:
        """Write the report as Markdown and return the file path."""
        lines = self._build(report)
        content = "\n".join(lines) + "\n"
        with open(output_path, "w") as f:
            f.write(content)
        return output_path

    def render_string(self, report: SwarmReport) -> str:
        """Return the Markdown content as a string (no file I/O)."""
        return "\n".join(self._build(report)) + "\n"

    def _build(self, report: SwarmReport) -> list[str]:
        lines: list[str] = []

        # -- Header ----------------------------------------------------
        score = report.swarm_score
        if score >= 75:
            score_badge = f"\U0001f7e2 {score}/100 — {report.certification_level}"
        elif score >= 50:
            score_badge = f"\U0001f7e1 {score}/100 — {report.certification_level}"
        else:
            score_badge = f"\U0001f534 {score}/100 — {report.certification_level}"

        lines.append(f"# \U0001f9ea Swarm Reliability Report — {report.swarm_name}")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| **Swarm** | {report.swarm_name} |")
        lines.append(f"| **Framework** | {report.framework} |")
        lines.append(f"| **Agents** | {report.agent_count} |")
        lines.append(f"| **Edges** | {report.edge_count} |")
        lines.append(f"| **Swarm Score** | {score_badge} |")
        lines.append(f"| **Duration** | {report.total_duration_ms:.0f}ms |")
        lines.append("")

        # -- Test results table ----------------------------------------
        lines.append("## Test Results")
        lines.append("")
        lines.append("| Test | Status | Findings | Critical | High | Duration |")
        lines.append("|------|--------|----------|----------|------|----------|")
        for r in report.test_results:
            icon = _STATUS_ICON.get(r.status, "")
            sev = r.severity_count()
            crit = sev.get("critical", 0)
            high = sev.get("high", 0)
            crit_str = f"**{crit}**" if crit else "0"
            high_str = f"**{high}**" if high else "0"
            lines.append(
                f"| {r.test_name} | {icon} {r.status.value.upper()} "
                f"| {len(r.findings)} | {crit_str} | {high_str} "
                f"| {r.duration_ms:.1f}ms |"
            )
        lines.append("")

        # -- Agent health scores ---------------------------------------
        if report.agent_scores:
            lines.append("## Agent Health Scores")
            lines.append("")
            lines.append("| Agent | Score | Status | Details |")
            lines.append("|-------|-------|--------|---------|")
            sorted_scores = sorted(report.agent_scores.values(), key=lambda s: s.score)
            for hs in sorted_scores:
                if hs.score >= 70:
                    score_str = f"\U0001f7e2 {hs.score}/100"
                elif hs.score >= 40:
                    score_str = f"\U0001f7e1 {hs.score}/100"
                else:
                    score_str = f"\U0001f534 {hs.score}/100"
                reasons = ", ".join(hs.reasons) if hs.reasons else "no issues"
                lines.append(
                    f"| {hs.agent_name} | {score_str} " f"| {hs.status_icon} | {reasons} |"
                )
            lines.append("")

        # -- Findings (top 10) ----------------------------------------
        all_findings = report.all_findings
        if all_findings:
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
            top = sorted_findings[:10]
            lines.append(f"## Top Findings ({len(all_findings)} total)")
            lines.append("")
            for i, finding in enumerate(top, 1):
                badge = _SEVERITY_BADGE.get(finding.severity, finding.severity.value)
                lines.append(f"### {i}. {badge} — {finding.title}")
                lines.append("")
                lines.append(f"**Test:** {finding.test_name}")
                lines.append("")
                lines.append(finding.description)
                lines.append("")
                if finding.remediation:
                    lines.append(f"> **Remediation:** {finding.remediation}")
                    lines.append("")
            if len(all_findings) > 10:
                lines.append(
                    f"*... and {len(all_findings) - 10} more findings "
                    f"(see full JSON/HTML report)*"
                )
                lines.append("")

        # -- Graph metrics ---------------------------------------------
        if report.graph_metrics:
            gm = report.graph_metrics
            lines.append("## Graph Metrics")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Nodes | {gm.get('node_count', '?')} |")
            lines.append(f"| Edges | {gm.get('edge_count', '?')} |")
            lines.append(f"| Density | {gm.get('density', 0):.4f} |")
            lines.append(f"| Cycles | {gm.get('cycle_count', 0)} |")
            lines.append(f"| SPOFs | {gm.get('single_points_of_failure', 0)} |")
            lines.append(f"| Critical Path | {gm.get('critical_path_length', 0)} hops |")
            connected = gm.get("is_weakly_connected", False)
            lines.append(f"| Weakly Connected | {'Yes' if connected else 'No'} |")
            lines.append("")

        # -- Footer ----------------------------------------------------
        try:
            from swarm_test import __version__
        except ImportError:
            __version__ = "unknown"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append("---")
        lines.append("")
        lines.append(
            f"*Generated by [swarm-test](https://github.com/surajkumar811/swarm-test) "
            f"v{__version__} at {ts}*"
        )

        return lines
