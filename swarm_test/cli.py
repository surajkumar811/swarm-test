"""CLI entry point for swarm-test."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option(package_name="swarm-test", prog_name="swarm-test")
def cli() -> None:
    """swarm-test — Reliability testing framework for multi-agent AI systems."""


@cli.command("probe")
@click.argument("script", type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option("--output", "-o", default=None, help="Output HTML report path (e.g. report.html)")
@click.option("--json-output", "-j", default=None, help="Output JSON report path")
@click.option(
    "--swarm-var",
    default="crew",
    show_default=True,
    help="Variable name of the swarm object in the script",
)
@click.option("--name", default=None, help="Override swarm name in report")
@click.option(
    "--fail-on-critical",
    is_flag=True,
    default=False,
    help="Exit with code 1 if CRITICAL findings exist",
)
@click.option(
    "--graph",
    is_flag=True,
    default=False,
    help="Print ASCII agent interaction graph after the report",
)
@click.option("--markdown", "-m", default=None, help="Output Markdown report path (e.g. report.md)")
def probe(
    script: str,
    output: str | None,
    json_output: str | None,
    swarm_var: str,
    name: str | None,
    fail_on_critical: bool,
    graph: bool,
    markdown: str | None,
) -> None:
    """Load a Python SCRIPT, extract the swarm object, and run all reliability tests."""
    import importlib.util

    console.print(f"[bold blue]swarm-test probe[/bold blue] — loading [cyan]{script}[/cyan]")

    spec = importlib.util.spec_from_file_location("_swarm_script", script)
    if spec is None or spec.loader is None:
        console.print(f"[red]Cannot load script: {script}[/red]")
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        console.print(f"[red]Error executing script: {exc}[/red]")
        sys.exit(1)

    swarm = getattr(module, swarm_var, None)
    if swarm is None:
        console.print(
            f"[yellow]Variable '{swarm_var}' not found in script. "
            "Running static graph analysis.[/yellow]"
        )

    from swarm_test.core.probe import SwarmProbe

    probe_obj = SwarmProbe(
        swarm,
        swarm_name=name or Path(script).stem,
    )
    report = probe_obj.run_all()
    report.print_summary()

    if graph:
        report.print_graph(graph=probe_obj.graph)

    if output:
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, output)
        console.print(f"\n[green]HTML report saved to:[/green] {path}")

    if json_output:
        report.to_json(json_output, graph=probe_obj.graph)
        console.print(f"[green]JSON report saved to:[/green] {json_output}")

    if markdown:
        report.to_markdown(markdown)
        console.print(f"[green]Markdown report saved to:[/green] {markdown}")

    if fail_on_critical and report.all_findings:
        from swarm_test.core.models import Severity

        has_critical = any(f.severity == Severity.CRITICAL for f in report.all_findings)
        if has_critical:
            console.print("[red]CRITICAL findings detected — exiting with code 1[/red]")
            sys.exit(1)


@cli.command("scan")
@click.option(
    "--agents",
    "-a",
    required=True,
    help="Comma-separated agent names (e.g. 'Researcher,Analyst,Writer')",
)
@click.option(
    "--edges",
    "-e",
    required=True,
    help="Comma-separated edges: 'A>B' for one-way, 'A<>B' for bidirectional",
)
@click.option("--name", default="cli-swarm", show_default=True, help="Swarm name")
@click.option("--html", default=None, help="Output HTML report path")
@click.option("--json", "json_output", default=None, help="Output JSON report path")
@click.option("--markdown", "-m", default=None, help="Output Markdown report path")
@click.option(
    "--graph",
    is_flag=True,
    default=False,
    help="Print ASCII agent interaction graph",
)
@click.option(
    "--fail-on",
    type=click.Choice(["critical", "high", "medium", "low", "info"], case_sensitive=False),
    default=None,
    help="Exit with code 1 if findings at this severity or above exist",
)
def scan(
    agents: str,
    edges: str,
    name: str,
    html: str | None,
    json_output: str | None,
    markdown: str | None,
    graph: bool,
    fail_on: str | None,
) -> None:
    """Quick scan: test any agent topology in seconds, no Python needed.

    \b
    Examples:
      swarm-test scan -a "Researcher,Analyst,Writer" -e "Researcher>Analyst,Analyst>Writer"
      swarm-test scan -a "Hub,A,B,C" -e "Hub<>A,Hub<>B,Hub<>C" --html report.html
      swarm-test scan -a "X,Y,Z" -e "X>Y,Y>Z,Z>X" --fail-on high
    """
    from swarm_test.core.models import AgentNode, EventType, InteractionEvent, Severity
    from swarm_test.core.probe import SwarmProbe

    # Parse agents
    agent_names = [a.strip() for a in agents.split(",") if a.strip()]
    if not agent_names:
        console.print("[red]No agents provided.[/red]")
        sys.exit(1)

    agent_nodes: dict[str, AgentNode] = {}
    for ag in agent_names:
        agent_nodes[ag] = AgentNode(name=ag, role="unknown")

    # Parse edges
    edge_specs = [e.strip() for e in edges.split(",") if e.strip()]
    event_list: list[InteractionEvent] = []

    def _ensure(name: str) -> AgentNode:
        if name not in agent_nodes:
            agent_nodes[name] = AgentNode(name=name, role="unknown")
        return agent_nodes[name]

    for spec in edge_specs:
        if "<>" in spec:
            parts = spec.split("<>", 1)
            src_node = _ensure(parts[0].strip())
            dst_node = _ensure(parts[1].strip())
            event_list.append(
                InteractionEvent(
                    source_agent_id=src_node.id,
                    target_agent_id=dst_node.id,
                    event_type=EventType.TASK_DELEGATE,
                    payload={"source": "cli"},
                )
            )
            event_list.append(
                InteractionEvent(
                    source_agent_id=dst_node.id,
                    target_agent_id=src_node.id,
                    event_type=EventType.AGENT_RESPONSE,
                    payload={"source": "cli"},
                )
            )
        elif ">" in spec:
            parts = spec.split(">", 1)
            src_node = _ensure(parts[0].strip())
            dst_node = _ensure(parts[1].strip())
            event_list.append(
                InteractionEvent(
                    source_agent_id=src_node.id,
                    target_agent_id=dst_node.id,
                    event_type=EventType.TASK_DELEGATE,
                    payload={"source": "cli"},
                )
            )
        else:
            console.print(f"[yellow]Skipping invalid edge '{spec}' (use A>B or A<>B)[/yellow]")

    console.print(
        f"[bold blue]swarm-test scan[/bold blue] — "
        f"[cyan]{len(agent_nodes)}[/cyan] agents, "
        f"[cyan]{len(event_list)}[/cyan] edges"
    )

    probe_obj = SwarmProbe(
        swarm_name=name,
        agents=list(agent_nodes.values()),
        events=event_list,
    )
    report = probe_obj.run_all()
    report.print_summary()

    if graph:
        report.print_graph(graph=probe_obj.graph)

    if html:
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, html)
        console.print(f"[green]HTML report saved to:[/green] {path}")

    if json_output:
        report.to_json(json_output, graph=probe_obj.graph)
        console.print(f"[green]JSON report saved to:[/green] {json_output}")

    if markdown:
        report.to_markdown(markdown)
        console.print(f"[green]Markdown report saved to:[/green] {markdown}")

    if fail_on and report.all_findings:
        severity_order = [s.value for s in Severity]
        threshold_idx = severity_order.index(fail_on.lower())
        has_match = any(
            severity_order.index(f.severity.value) <= threshold_idx for f in report.all_findings
        )
        if has_match:
            console.print(
                f"[red]Findings at {fail_on.upper()} or above detected "
                f"— exiting with code 1[/red]"
            )
            sys.exit(1)


@cli.command("run")
@click.argument(
    "script",
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    required=False,
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    default=None,
    help="Path to a YAML config file (default: auto-discover .swarmtest.yml in cwd)",
)
@click.option(
    "--agents",
    "-a",
    default=None,
    help="Comma-separated agent names (e.g. 'Researcher,Analyst,Writer')",
)
@click.option(
    "--edges",
    "-e",
    default=None,
    help="Comma-separated edges: 'A>B' for one-way, 'A<>B' for bidirectional",
)
@click.option(
    "--swarm-var",
    default="crew",
    show_default=True,
    help="Variable name of the swarm object in the script",
)
@click.option("--name", default="swarm-run", show_default=True, help="Swarm name")
@click.option(
    "--fail-on-severity",
    type=click.Choice(
        ["critical", "high", "medium", "low", "info", "none"], case_sensitive=False
    ),
    default=None,
    help="Override config: minimum severity that triggers exit code 1",
)
@click.option(
    "--max-blast-radius",
    type=float,
    default=None,
    help="Override config: blast-radius threshold 0.0-1.0",
)
@click.option(
    "--output-format",
    type=click.Choice(["console", "json", "markdown", "html"], case_sensitive=False),
    default=None,
    help="Override config: output format",
)
@click.option(
    "--output-path",
    default=None,
    help="Override config: file path for json/markdown/html output",
)
@click.option(
    "--quick-scan",
    is_flag=True,
    default=None,
    help="Override config: enable quick scan mode",
)
@click.option(
    "--strict",
    is_flag=True,
    default=None,
    help="Override config: treat any finding as a failure",
)
def run_cmd(
    script: str | None,
    config_path: str | None,
    agents: str | None,
    edges: str | None,
    swarm_var: str,
    name: str,
    fail_on_severity: str | None,
    max_blast_radius: float | None,
    output_format: str | None,
    output_path: str | None,
    quick_scan: bool | None,
    strict: bool | None,
) -> None:
    """Run swarm-test with a YAML config file (auto-discovered) plus optional CLI overrides.

    \b
    Examples:
      swarm-test run --config .swarmtest.yml
      swarm-test run -a "A,B,C" -e "A>B,B>C"
      swarm-test run my_crew.py --config custom.yml --strict
    """
    from swarm_test.config import find_config_path, load_config, merge_cli_args

    # ---- Load config ---------------------------------------------------
    try:
        config = load_config(path=config_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Config error: {exc}[/red]")
        sys.exit(2)

    discovered = Path(config_path) if config_path else find_config_path()
    if discovered is not None:
        console.print(f"[dim]Loaded config from {discovered}[/dim]")

    # ---- Merge CLI overrides ------------------------------------------
    cli_overrides: dict[str, object] = {
        "fail_on_severity": fail_on_severity.lower() if fail_on_severity else None,
        "max_blast_radius": max_blast_radius,
        "output_format": output_format.lower() if output_format else None,
        "output_path": output_path,
        "quick_scan": quick_scan,
        "strict": strict,
    }
    try:
        config = merge_cli_args(config, cli_overrides)
    except ValueError as exc:
        console.print(f"[red]Config error: {exc}[/red]")
        sys.exit(2)

    # ---- Build SwarmProbe ---------------------------------------------
    from swarm_test.core.models import AgentNode, EventType, InteractionEvent
    from swarm_test.core.probe import SwarmProbe

    probe_obj: SwarmProbe
    if script:
        import importlib.util

        console.print(f"[bold blue]swarm-test run[/bold blue] — loading [cyan]{script}[/cyan]")
        spec = importlib.util.spec_from_file_location("_swarm_script", script)
        if spec is None or spec.loader is None:
            console.print(f"[red]Cannot load script: {script}[/red]")
            sys.exit(2)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            console.print(f"[red]Error executing script: {exc}[/red]")
            sys.exit(2)
        swarm = getattr(module, swarm_var, None)
        probe_obj = SwarmProbe(
            swarm,
            swarm_name=name if name != "swarm-run" else Path(script).stem,
            config=config,
        )
    elif agents and edges:
        agent_nodes: dict[str, AgentNode] = {}
        for ag in (a.strip() for a in agents.split(",") if a.strip()):
            agent_nodes[ag] = AgentNode(name=ag, role="unknown")

        def _ensure(n: str) -> AgentNode:
            if n not in agent_nodes:
                agent_nodes[n] = AgentNode(name=n, role="unknown")
            return agent_nodes[n]

        event_list: list[InteractionEvent] = []
        for spec_str in (e.strip() for e in edges.split(",") if e.strip()):
            if "<>" in spec_str:
                parts = spec_str.split("<>", 1)
                s_node = _ensure(parts[0].strip())
                d_node = _ensure(parts[1].strip())
                event_list.append(
                    InteractionEvent(
                        source_agent_id=s_node.id,
                        target_agent_id=d_node.id,
                        event_type=EventType.TASK_DELEGATE,
                    )
                )
                event_list.append(
                    InteractionEvent(
                        source_agent_id=d_node.id,
                        target_agent_id=s_node.id,
                        event_type=EventType.AGENT_RESPONSE,
                    )
                )
            elif ">" in spec_str:
                parts = spec_str.split(">", 1)
                s_node = _ensure(parts[0].strip())
                d_node = _ensure(parts[1].strip())
                event_list.append(
                    InteractionEvent(
                        source_agent_id=s_node.id,
                        target_agent_id=d_node.id,
                        event_type=EventType.TASK_DELEGATE,
                    )
                )
        probe_obj = SwarmProbe(
            swarm_name=name,
            agents=list(agent_nodes.values()),
            events=event_list,
            config=config,
        )
    else:
        console.print(
            "[red]Provide either a SCRIPT path or both --agents and --edges to run.[/red]"
        )
        sys.exit(2)

    # ---- Run tests -----------------------------------------------------
    try:
        report = probe_obj.run_all()
    except Exception as exc:
        console.print(f"[red]Probe failed: {exc}[/red]")
        sys.exit(2)

    # ---- Emit output ---------------------------------------------------
    fmt = config.output_format
    out = config.output_path
    if fmt == "console":
        report.print_summary()
    elif fmt == "json":
        report.print_summary()
        target = out or "swarm_report.json"
        report.to_json(target, graph=probe_obj.graph)
        console.print(f"[green]JSON report saved to:[/green] {target}")
    elif fmt == "markdown":
        report.print_summary()
        target = out or "swarm_report.md"
        report.to_markdown(target)
        console.print(f"[green]Markdown report saved to:[/green] {target}")
    elif fmt == "html":
        report.print_summary()
        from swarm_test.reporters.html import HtmlReporter

        target = out or "swarm_report.html"
        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, target)
        console.print(f"[green]HTML report saved to:[/green] {path}")

    # ---- Threshold check ----------------------------------------------
    if SwarmProbe.check_thresholds(config, report):
        console.print(
            f"[red]Findings exceed thresholds "
            f"(fail_on_severity={config.fail_on_severity}, "
            f"max_blast_radius={config.max_blast_radius}) — exiting with code 1[/red]"
        )
        sys.exit(1)
    sys.exit(0)


@cli.command("compare")
@click.argument("before", type=click.Path(exists=True))
@click.argument("after", type=click.Path(exists=True))
def compare(before: str, after: str) -> None:
    """Compare two JSON reports and show what improved, regressed, or changed."""
    from swarm_test.comparison import ReportComparator, load_report

    try:
        before_data = load_report(before)
        after_data = load_report(after)
    except Exception as exc:
        console.print(f"[red]Failed to load reports: {exc}[/red]")
        sys.exit(1)

    comparator = ReportComparator()
    result = comparator.compare(before_data, after_data)
    comparator.print_comparison(result, console)


@cli.command("version")
def version_cmd() -> None:
    """Print version information."""
    try:
        from importlib.metadata import version

        v = version("swarm-test")
    except Exception:
        v = "0.1.0"
    console.print(f"swarm-test [cyan]{v}[/cyan]")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
