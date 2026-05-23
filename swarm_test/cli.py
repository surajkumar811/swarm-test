"""CLI entry point for swarm-test."""

from __future__ import annotations

import json
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
def probe(
    script: str,
    output: str | None,
    json_output: str | None,
    swarm_var: str,
    name: str | None,
    fail_on_critical: bool,
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

    if output:
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, output)
        console.print(f"\n[green]HTML report saved to:[/green] {path}")

    if json_output:
        data = report.model_dump(mode="json")
        with open(json_output, "w") as f:
            json.dump(data, f, indent=2, default=str)
        console.print(f"[green]JSON report saved to:[/green] {json_output}")

    if fail_on_critical and report.all_findings:
        from swarm_test.core.models import Severity

        has_critical = any(f.severity == Severity.CRITICAL for f in report.all_findings)
        if has_critical:
            console.print("[red]CRITICAL findings detected — exiting with code 1[/red]")
            sys.exit(1)


@cli.command("scan")
@click.option("--agents", "-a", multiple=True, help="Agent names (can specify multiple)")
@click.option("--edges", "-e", multiple=True, help="Edges as 'source:target' pairs")
@click.option("--output", "-o", default=None, help="Output HTML report path")
@click.option("--name", default="cli-swarm", show_default=True, help="Swarm name")
def scan(agents: tuple, edges: tuple, output: str | None, name: str) -> None:
    """Run a static graph scan from agent names and edge pairs without a live swarm."""
    from swarm_test.core.models import AgentNode, EventType, InteractionEvent
    from swarm_test.core.probe import SwarmProbe

    agent_nodes = {}
    for ag in agents:
        node = AgentNode(name=ag, role="unknown")
        agent_nodes[ag] = node

    event_list = []
    for edge in edges:
        if ":" not in edge:
            console.print(
                f"[yellow]Skipping invalid edge format '{edge}' (expected 'source:target')[/yellow]"
            )
            continue
        src_name, dst_name = edge.split(":", 1)
        if src_name not in agent_nodes:
            agent_nodes[src_name] = AgentNode(name=src_name, role="unknown")
        if dst_name not in agent_nodes:
            agent_nodes[dst_name] = AgentNode(name=dst_name, role="unknown")
        event_list.append(
            InteractionEvent(
                source_agent_id=agent_nodes[src_name].id,
                target_agent_id=agent_nodes[dst_name].id,
                event_type=EventType.TASK_DELEGATE,
                payload={"source": "cli"},
            )
        )

    probe_obj = SwarmProbe(
        swarm_name=name,
        agents=list(agent_nodes.values()),
        events=event_list,
    )
    report = probe_obj.run_all()
    report.print_summary()

    if output:
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, output)
        console.print(f"\n[green]HTML report saved to:[/green] {path}")


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
