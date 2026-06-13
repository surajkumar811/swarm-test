"""GitHub Actions reporter — workflow command annotations + step summary."""

from __future__ import annotations

import os
from collections.abc import Iterable

from swarm_test.core.models import Finding, Severity, SwarmReport, redundancy_level

# Map finding severity → GitHub Actions workflow-command level.
# https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions
_SEVERITY_TO_COMMAND: dict[Severity, str] = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "warning",
    Severity.MEDIUM: "notice",
    Severity.LOW: "notice",
    Severity.INFO: "notice",
}


def swarm_score(report: SwarmReport) -> float:
    """Invert risk_score so higher = healthier (0-100)."""
    return round(max(0.0, min(100.0, 100.0 - report.risk_score)), 1)


def certification_level(score: float) -> str:
    """Map the 0-100 swarm score to a certification badge."""
    if score >= 90:
        return "Production-ready"
    if score >= 75:
        return "Stable"
    if score >= 50:
        return "Caution"
    return "Critical risk"


def _escape(value: str) -> str:
    """Escape `%`, `\\r`, `\\n` for GitHub Actions workflow commands."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def format_annotation(finding: Finding) -> str:
    """Render a single Finding as a GitHub Actions ``::level::`` workflow command."""
    level = _SEVERITY_TO_COMMAND.get(finding.severity, "notice")
    title = _escape(finding.title or finding.test_name)
    description = _escape(finding.description or "")
    message = f"[{finding.test_name}] {title}"
    if description:
        message = f"{message} — {description}"
    return f"::{level} title={title}::{message}"


class GitHubReporter:
    """Emit GitHub Actions annotations + a ``$GITHUB_STEP_SUMMARY`` markdown report."""

    # ------------------------------------------------------------------
    # Annotations
    # ------------------------------------------------------------------

    def annotations(self, findings: Iterable[Finding]) -> list[str]:
        """Return the list of GitHub Actions workflow-command lines."""
        return [format_annotation(f) for f in findings]

    def emit_annotations(self, report: SwarmReport, *, stream=None) -> None:
        """Print each finding as a ``::error::`` / ``::warning::`` / ``::notice::`` line."""
        import sys

        out = stream or sys.stdout
        for line in self.annotations(report.all_findings):
            print(line, file=out)

    # ------------------------------------------------------------------
    # Step summary
    # ------------------------------------------------------------------

    def render_summary(self, report: SwarmReport) -> str:
        """Build a markdown report suitable for ``$GITHUB_STEP_SUMMARY``."""
        score = swarm_score(report)
        level = certification_level(score)
        lines: list[str] = []

        lines.append(f"## swarm-test — {report.swarm_name}")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Swarm Score | **{score:.0f} / 100** ({level}) |")
        lines.append(f"| Framework | {report.framework} |")
        lines.append(f"| Agents | {report.agent_count} |")
        lines.append(f"| Edges | {report.edge_count} |")
        lines.append(f"| Findings | {len(report.all_findings)} |")
        lines.append(f"| Duration | {report.total_duration_ms:.0f} ms |")
        lines.append("")

        # ---- Test results -------------------------------------------
        lines.append("### Test Results")
        lines.append("")
        lines.append("| Test | Status | Findings | Critical | High |")
        lines.append("|------|--------|----------|----------|------|")
        for result in report.test_results:
            sev = result.severity_count()
            lines.append(
                f"| {result.test_name} "
                f"| {result.status.value.upper()} "
                f"| {len(result.findings)} "
                f"| {sev.get('critical', 0)} "
                f"| {sev.get('high', 0)} |"
            )
        lines.append("")

        # ---- Top findings -------------------------------------------
        actionable: list[Finding] = [
            f for f in report.all_findings if f.severity in (Severity.CRITICAL, Severity.HIGH)
        ]
        if actionable:
            lines.append(f"### Top findings ({len(actionable)} critical/high)")
            lines.append("")
            order = [Severity.CRITICAL, Severity.HIGH]
            actionable.sort(key=lambda f: order.index(f.severity))
            for finding in actionable[:10]:
                badge = "CRITICAL" if finding.severity == Severity.CRITICAL else "HIGH"
                lines.append(f"- **[{badge}] {finding.title}** — {finding.description}")
                if finding.remediation:
                    lines.append(f"  - _Remediation:_ {finding.remediation}")
            if len(actionable) > 10:
                lines.append(f"- _… and {len(actionable) - 10} more (see full report)_")
            lines.append("")

        # ---- Redundancy summary -------------------------------------
        if report.redundancy_scores:
            spofs: list[tuple[str, float]] = []
            for agent_id, r_score in report.redundancy_scores.items():
                if r_score <= 20:
                    score_obj = report.agent_scores.get(agent_id)
                    name = score_obj.agent_name if score_obj else agent_id
                    spofs.append((name, float(r_score)))

            lines.append("### Agent Redundancy")
            lines.append("")
            if spofs:
                lines.append(f"**{len(spofs)} single point(s) of failure detected:**")
                lines.append("")
                for name, r in sorted(spofs, key=lambda x: x[1]):
                    lines.append(f"- `{name}` — score {r:.0f}/100 ({redundancy_level(r)})")
            else:
                lines.append("No single points of failure detected.")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append(
            "_Report generated by [swarm-test](https://github.com/surajkumar811/swarm-test)._"
        )
        return "\n".join(lines) + "\n"

    def write_step_summary(self, report: SwarmReport, *, path: str | None = None) -> str | None:
        """Append a step summary to the file at ``$GITHUB_STEP_SUMMARY`` (or ``path``).

        Returns the path written to, or ``None`` if neither was set.
        """
        target = path or os.environ.get("GITHUB_STEP_SUMMARY")
        if not target:
            return None
        content = self.render_summary(report)
        with open(target, "a", encoding="utf-8") as f:
            f.write(content)
        return target
