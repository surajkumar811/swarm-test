"""CLI entry point for swarm-test."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from typing import Any

import click
from rich.console import Console

console = Console()


def _resolve_verbosity(
    quiet: bool,
    verbose: bool,
    *,
    default: Any = "normal",
) -> Any:
    """Map --quiet/--verbose flags to a verbosity string.

    Returns ``default`` when neither flag is set. ``--quiet`` wins over
    ``--verbose`` if both are passed.
    """
    if quiet:
        return "quiet"
    if verbose:
        return "verbose"
    return default


def _announce_plugins(probe_obj: Any, verbosity: str) -> None:
    """Print 'Loaded N plugin(s)' if the probe discovered any."""
    if verbosity == "quiet":
        return
    try:
        count = len(probe_obj.plugin_registry)
    except Exception:
        return
    if count > 0:
        console.print(f"[dim]Loaded {count} plugin(s)[/dim]")


def _open_in_browser(path: str) -> None:
    """Open a local HTML file in the default browser. Failure is non-fatal."""
    try:
        url = Path(path).resolve().as_uri()
        webbrowser.open(url)
    except Exception as exc:
        console.print(f"[yellow]Could not open browser ({exc}); open {path} manually.[/yellow]")


# Role tokens accepted in CLI "-a Name:role" syntax that map to a declared
# intentional hub. Anything outside this set leaves intentional_role unset and
# the role still flows into the lexical-hint classifier as a soft signal.
_INTENTIONAL_ORCHESTRATOR_TOKENS = frozenset(
    {
        "orchestrator",
        "coordinator",
        "manager",
        "supervisor",
        "dispatcher",
        "planner",
        "hub",
    }
)
_INTENTIONAL_AGGREGATOR_TOKENS = frozenset(
    {"aggregator", "aggregate", "collector", "consolidator", "reducer"}
)


def _role_text_to_intentional(role_text: str) -> str | None:
    """Map a free-form role token to an intentional AgentRole, if any."""
    text = (role_text or "").strip().lower()
    if not text or text == "unknown":
        return None
    if text in _INTENTIONAL_ORCHESTRATOR_TOKENS:
        return "ORCHESTRATOR"
    if text in _INTENTIONAL_AGGREGATOR_TOKENS:
        return "AGGREGATOR"
    return None


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
@click.option(
    "--quiet",
    "-q",
    "quiet",
    is_flag=True,
    default=False,
    help="Print only the headline verdict line.",
)
@click.option(
    "--verbose",
    "-V",
    "verbose",
    is_flag=True,
    default=False,
    help="Print every finding plus graph metrics and full health/redundancy tables.",
)
@click.option(
    "--open",
    "open_report",
    is_flag=True,
    default=False,
    help="Open the generated HTML report in the default browser.",
)
@click.option(
    "--no-history",
    is_flag=True,
    default=False,
    help="Skip writing this run to .swarmtest-history and skip trend display.",
)
def probe(
    script: str,
    output: str | None,
    json_output: str | None,
    swarm_var: str,
    name: str | None,
    fail_on_critical: bool,
    graph: bool,
    markdown: str | None,
    quiet: bool,
    verbose: bool,
    open_report: bool,
    no_history: bool,
) -> None:
    """Load a Python SCRIPT, extract the swarm object, and run all reliability tests."""
    import importlib.util

    verbosity = _resolve_verbosity(quiet, verbose)
    if verbosity != "quiet":
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
        # Try common alternative variable names (e.g. autogen "groupchat" / "manager")
        for fallback in ("groupchat", "manager", "group_chat", "swarm", "graph", "agents"):
            candidate = getattr(module, fallback, None)
            if candidate is not None:
                swarm = candidate
                console.print(
                    f"[dim]Variable '{swarm_var}' not found; " f"using '{fallback}' instead.[/dim]"
                )
                break
    if swarm is None:
        console.print(
            f"[yellow]Variable '{swarm_var}' not found in script. "
            "Running static graph analysis.[/yellow]"
        )

    from swarm_test.core.probe import SwarmProbe

    probe_obj = SwarmProbe(
        swarm,
        swarm_name=name or Path(script).stem,
        enable_history=not no_history,
    )
    _announce_plugins(probe_obj, verbosity)
    report = probe_obj.run_all()
    report.print_summary(verbosity=verbosity)

    if graph and verbosity != "quiet":
        report.print_graph(graph=probe_obj.graph)

    if output:
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, output)
        if verbosity != "quiet":
            console.print(f"\n[green]HTML report saved to:[/green] {path}")
        if open_report:
            _open_in_browser(path)

    if json_output:
        report.to_json(json_output, graph=probe_obj.graph)
        if verbosity != "quiet":
            console.print(f"[green]JSON report saved to:[/green] {json_output}")

    if markdown:
        report.to_markdown(markdown)
        if verbosity != "quiet":
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
    "--fail-on-severity",
    "--fail-on",
    "fail_on",
    type=click.Choice(["critical", "high", "medium", "low", "info", "none"], case_sensitive=False),
    default=None,
    help="Exit with code 1 if findings at this severity or above exist (--fail-on is an alias).",
)
@click.option(
    "--ci",
    "ci",
    is_flag=True,
    default=False,
    help=(
        "CI gate mode: print the one-line summary and fail the build (exit 1) "
        "when findings meet/exceed the threshold. Defaults the threshold to "
        "'high' unless --fail-on-severity or .swarmtest.yml sets it."
    ),
)
@click.option(
    "--output-format",
    type=click.Choice(["console", "json"], case_sensitive=False),
    default=None,
    help="Print the report as console text (default) or machine-readable JSON to stdout.",
)
@click.option(
    "--quiet",
    "-q",
    "quiet",
    is_flag=True,
    default=False,
    help="Print only the headline verdict line.",
)
@click.option(
    "--verbose",
    "-V",
    "verbose",
    is_flag=True,
    default=False,
    help="Print every finding plus graph metrics and full health/redundancy tables.",
)
@click.option(
    "--open",
    "open_report",
    is_flag=True,
    default=False,
    help="Open the generated HTML report in the default browser.",
)
@click.option(
    "--no-history",
    is_flag=True,
    default=False,
    help="Skip writing this run to .swarmtest-history and skip trend display.",
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
    ci: bool,
    output_format: str | None,
    quiet: bool,
    verbose: bool,
    open_report: bool,
    no_history: bool,
) -> None:
    """Quick scan: test any agent topology in seconds, no Python needed.

    \b
    Examples:
      swarm-test scan -a "Researcher,Analyst,Writer" -e "Researcher>Analyst,Analyst>Writer"
      swarm-test scan -a "Hub,A,B,C" -e "Hub<>A,Hub<>B,Hub<>C" --html report.html
      swarm-test scan -a "X,Y,Z" -e "X>Y,Y>Z,Z>X" --ci   # CI gate: exit 1 on high+ findings
    """
    from swarm_test.config import config_file_keys, load_config
    from swarm_test.core.models import AgentNode, EventType, InteractionEvent, Severity
    from swarm_test.core.probe import SwarmProbe

    # ---- CI gate mode --------------------------------------------------
    # Mirror `run --ci`: concise one-line output + a default 'high' threshold
    # that yields to an explicit flag or a .swarmtest.yml value.
    config = None
    if ci:
        if not quiet and not verbose:
            quiet = True
        config = load_config()
        if output_format is None:
            output_format = "console"
        if fail_on is None:
            fail_on = "high" if "fail_on_severity" not in config_file_keys() else None

    verbosity = _resolve_verbosity(quiet, verbose)
    # In CI mode, honour the config file's fail_on_severity when the user
    # gave no explicit threshold (config wins over the 'high' default above).
    if ci and fail_on is None and config is not None:
        fail_on = config.fail_on_severity

    # Parse agents. Accepts plain names ("Hub") or "name:role" pairs
    # ("Hub:orchestrator") so users can declare an intentional hub on the
    # command line without writing Python. Role tokens recognised as hubs
    # (orchestrator/coordinator/manager/supervisor/dispatcher/planner) are
    # promoted to intentional_role=ORCHESTRATOR; aggregator/aggregate become
    # intentional_role=AGGREGATOR. Other roles are kept as a free-form label
    # used by the lexical-hint classifier.
    agent_specs = [a.strip() for a in agents.split(",") if a.strip()]
    if not agent_specs:
        console.print("[red]No agents provided.[/red]")
        sys.exit(1)

    agent_nodes: dict[str, AgentNode] = {}
    for spec in agent_specs:
        if ":" in spec:
            name_part, _, role_part = spec.partition(":")
            agent_name = name_part.strip()
            role_text = role_part.strip().lower()
        else:
            agent_name = spec
            role_text = "unknown"
        intentional = _role_text_to_intentional(role_text)
        agent_nodes[agent_name] = AgentNode(
            name=agent_name,
            role=role_text or "unknown",
            intentional_role=intentional,
        )

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

    if verbosity != "quiet":
        console.print(
            f"[bold blue]swarm-test scan[/bold blue] — "
            f"[cyan]{len(agent_nodes)}[/cyan] agents, "
            f"[cyan]{len(event_list)}[/cyan] edges"
        )

    probe_obj = SwarmProbe(
        swarm_name=name,
        agents=list(agent_nodes.values()),
        events=event_list,
        enable_history=not no_history,
        config=config,
    )
    _announce_plugins(probe_obj, verbosity)
    report = probe_obj.run_all()

    # ---- GitHub Actions integration (CI mode) -------------------------
    import os as _os

    if ci and _os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        from swarm_test.reporters.github import GitHubReporter

        gh_reporter = GitHubReporter()
        gh_reporter.emit_annotations(report)
        gh_reporter.write_step_summary(report)

    # ---- Emit report ---------------------------------------------------
    if (output_format or "console").lower() == "json":
        import json as _json

        # Emit only the JSON document to stdout so it parses cleanly.
        print(_json.dumps(report.to_json(graph=probe_obj.graph), indent=2))
    else:
        report.print_summary(verbosity=verbosity)

    if graph and verbosity != "quiet":
        report.print_graph(graph=probe_obj.graph)

    if html:
        from swarm_test.reporters.html import HtmlReporter

        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, html)
        if verbosity != "quiet":
            console.print(f"[green]HTML report saved to:[/green] {path}")
        if open_report:
            _open_in_browser(path)

    if json_output:
        report.to_json(json_output, graph=probe_obj.graph)
        if verbosity != "quiet":
            console.print(f"[green]JSON report saved to:[/green] {json_output}")

    if markdown:
        report.to_markdown(markdown)
        if verbosity != "quiet":
            console.print(f"[green]Markdown report saved to:[/green] {markdown}")

    if fail_on and fail_on.lower() != "none" and report.all_findings:
        severity_order = [s.value for s in Severity]
        threshold_idx = severity_order.index(fail_on.lower())
        has_match = any(
            severity_order.index(f.severity.value) <= threshold_idx for f in report.all_findings
        )
        if has_match:
            # In json mode stdout must stay clean; send the notice to stderr.
            msg_console = (
                Console(stderr=True) if (output_format or "console").lower() == "json" else console
            )
            msg_console.print(
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
    type=click.Choice(["critical", "high", "medium", "low", "info", "none"], case_sensitive=False),
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
    type=click.Choice(
        ["console", "json", "markdown", "html", "mermaid", "dot", "png"],
        case_sensitive=False,
    ),
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
@click.option(
    "--contracts",
    "contracts_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    default=None,
    help="Path to a YAML file of agent output contracts (enables contract_violation test)",
)
@click.option(
    "--github-action",
    is_flag=True,
    default=False,
    help=(
        "Emit GitHub Actions annotations and a step summary. "
        "Auto-enabled when GITHUB_ACTIONS=true."
    ),
)
@click.option(
    "--ci",
    "ci",
    is_flag=True,
    default=False,
    help=(
        "CI gate mode: print the one-line summary and fail the build (exit 1) "
        "when findings meet/exceed the severity threshold. Defaults the "
        "threshold to 'high' unless --fail-on-severity or .swarmtest.yml sets it."
    ),
)
@click.option(
    "--quiet",
    "-q",
    "quiet",
    is_flag=True,
    default=False,
    help="Print only the headline verdict line (perfect for CI scripts).",
)
@click.option(
    "--verbose",
    "-V",
    "verbose",
    is_flag=True,
    default=False,
    help="Print every finding plus graph metrics and full health/redundancy tables.",
)
@click.option(
    "--open",
    "open_report",
    is_flag=True,
    default=False,
    help="Open the generated HTML report in the default browser (with --output-format html).",
)
@click.option(
    "--no-history",
    is_flag=True,
    default=False,
    help="Disable historical tracking for this run (overrides config).",
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
    contracts_path: str | None,
    github_action: bool,
    ci: bool,
    quiet: bool,
    verbose: bool,
    open_report: bool,
    no_history: bool,
) -> None:
    """Run swarm-test with a YAML config file (auto-discovered) plus optional CLI overrides.

    \b
    Examples:
      swarm-test run --config .swarmtest.yml
      swarm-test run my_crew.py --config custom.yml --strict

    For an inline topology without a script or YAML, use the dedicated
    quick-scan subcommand instead:

    \b
      swarm-test scan -a "A,B,C" -e "A>B,B>C"
    """
    from swarm_test.config import (
        config_file_keys,
        find_config_path,
        load_config,
        merge_cli_args,
    )

    # ---- Load config ---------------------------------------------------
    try:
        config = load_config(path=config_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Config error: {exc}[/red]")
        sys.exit(2)

    # ---- CI gate mode --------------------------------------------------
    # --ci is a convenience preset: concise one-line output + a sensible
    # default severity threshold of 'high'. It never overrides an explicit
    # --fail-on-severity flag or a fail_on_severity set in .swarmtest.yml —
    # config still wins, so CI behaviour stays configurable.
    if ci:
        if not quiet and not verbose:
            quiet = True
        if fail_on_severity is None and "fail_on_severity" not in config_file_keys(config_path):
            fail_on_severity = "high"

    discovered = Path(config_path) if config_path else find_config_path()
    # We'll re-check verbosity after merging CLI overrides; only emit the
    # "loaded config" dim line outside of quiet mode.
    _early_verbosity = _resolve_verbosity(quiet, verbose)
    if discovered is not None and _early_verbosity != "quiet":
        console.print(f"[dim]Loaded config from {discovered}[/dim]")

    # ---- Merge CLI overrides ------------------------------------------
    cli_verbosity = _resolve_verbosity(quiet, verbose, default=None)
    cli_overrides: dict[str, object] = {
        "fail_on_severity": fail_on_severity.lower() if fail_on_severity else None,
        "max_blast_radius": max_blast_radius,
        "output_format": output_format.lower() if output_format else None,
        "output_path": output_path,
        "quick_scan": quick_scan,
        "strict": strict,
        "contracts_path": contracts_path,
        "output_verbosity": cli_verbosity,
        "history_enabled": False if no_history else None,
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

        if config.output_verbosity != "quiet":
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
        if swarm is None:
            for fallback in ("groupchat", "manager", "group_chat", "swarm", "graph", "agents"):
                candidate = getattr(module, fallback, None)
                if candidate is not None:
                    swarm = candidate
                    break
        probe_obj = SwarmProbe(
            swarm,
            swarm_name=name if name != "swarm-run" else Path(script).stem,
            config=config,
            contracts=config.contracts_path,
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
            contracts=config.contracts_path,
        )
    else:
        console.print(
            "[red]Provide either a SCRIPT path or both --agents and --edges to run.[/red]"
        )
        sys.exit(2)

    _announce_plugins(probe_obj, config.output_verbosity)

    # ---- Run tests -----------------------------------------------------
    try:
        report = probe_obj.run_all()
    except Exception as exc:
        console.print(f"[red]Probe failed: {exc}[/red]")
        sys.exit(2)

    # ---- GitHub Actions integration -----------------------------------
    import os as _os

    verbosity_final = config.output_verbosity
    in_github = github_action or _os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if in_github:
        from swarm_test.reporters.github import GitHubReporter

        gh_reporter = GitHubReporter()
        gh_reporter.emit_annotations(report)
        summary_path = gh_reporter.write_step_summary(report)
        if summary_path and verbosity_final != "quiet":
            console.print(f"[dim]GitHub step summary written to {summary_path}[/dim]")

    # ---- Emit output ---------------------------------------------------
    fmt = config.output_format
    out = config.output_path
    if fmt == "console":
        report.print_summary(verbosity=verbosity_final)
    elif fmt == "json":
        report.print_summary(verbosity=verbosity_final)
        target = out or "swarm_report.json"
        report.to_json(target, graph=probe_obj.graph)
        if verbosity_final != "quiet":
            console.print(f"[green]JSON report saved to:[/green] {target}")
    elif fmt == "markdown":
        report.print_summary(verbosity=verbosity_final)
        target = out or "swarm_report.md"
        report.to_markdown(target)
        if verbosity_final != "quiet":
            console.print(f"[green]Markdown report saved to:[/green] {target}")
    elif fmt == "html":
        report.print_summary(verbosity=verbosity_final)
        from swarm_test.reporters.html import HtmlReporter

        target = out or "swarm_report.html"
        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe_obj.graph, target)
        if verbosity_final != "quiet":
            console.print(f"[green]HTML report saved to:[/green] {path}")
        if open_report:
            _open_in_browser(path)
    elif fmt in ("mermaid", "dot"):
        from swarm_test.reporters import graph_export

        text = (
            graph_export.to_mermaid(probe_obj.graph, report=report)
            if fmt == "mermaid"
            else graph_export.to_dot(probe_obj.graph, report=report)
        )
        if out:
            with open(out, "w") as f:
                f.write(text)
            if verbosity_final != "quiet":
                console.print(f"[green]{fmt.upper()} graph saved to:[/green] {out}")
        else:
            console.print(text)
    elif fmt == "png":
        from swarm_test.reporters import graph_export

        if not out:
            console.print("[red]--output-path is required for PNG export.[/red]")
            sys.exit(2)
        try:
            graph_export.to_png(probe_obj.graph, report=report, output_path=out)
        except ImportError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(2)
        if verbosity_final != "quiet":
            console.print(f"[green]PNG graph saved to:[/green] {out}")

    # ---- Threshold check ----------------------------------------------
    if SwarmProbe.check_thresholds(config, report):
        if verbosity_final != "quiet":
            console.print(
                f"[red]Findings exceed thresholds "
                f"(fail_on_severity={config.fail_on_severity}, "
                f"max_blast_radius={config.max_blast_radius}) — exiting with code 1[/red]"
            )
        sys.exit(1)
    sys.exit(0)


@cli.group("plugins")
def plugins_group() -> None:
    """Manage and inspect installed swarm-test plugins."""


@plugins_group.command("list")
def plugins_list() -> None:
    """List all discovered swarm-test plugins."""
    from swarm_test.plugins import discover_plugins

    registry = discover_plugins()
    plugins = registry.list_plugins()
    if not plugins:
        console.print(
            "[yellow]No swarm-test plugins discovered.[/yellow]\n"
            "[dim]Install a plugin package (it must declare a "
            "'swarm_test.plugins' entry point).[/dim]"
        )
        return

    from rich.table import Table

    table = Table(title=f"Discovered Plugins ({len(plugins)})", show_lines=False)
    table.add_column("Name", style="bold cyan")
    table.add_column("Version", style="magenta")
    table.add_column("Author", style="dim")
    table.add_column("Description")
    for p in plugins:
        table.add_row(
            p.get("name", ""),
            p.get("version", ""),
            p.get("author", "") or "—",
            p.get("description", ""),
        )
    console.print(table)


@plugins_group.command("info")
@click.argument("name")
def plugins_info(name: str) -> None:
    """Show detailed information about a single plugin by NAME."""
    from swarm_test.plugins import discover_plugins

    registry = discover_plugins()
    plugin = registry.get(name)
    if plugin is None:
        console.print(f"[red]Plugin '{name}' not found.[/red]")
        installed = [p["name"] for p in registry.list_plugins()]
        if installed:
            console.print(f"[dim]Installed plugins: {', '.join(installed)}[/dim]")
        sys.exit(1)

    from rich.panel import Panel

    body = (
        f"[bold]Name:[/bold] {plugin.name}\n"
        f"[bold]Version:[/bold] {plugin.version}\n"
        f"[bold]Author:[/bold] {plugin.author or '—'}\n"
        f"[bold]Description:[/bold] {plugin.description}\n"
        f"[bold]Class:[/bold] {type(plugin).__module__}.{type(plugin).__name__}"
    )
    console.print(Panel(body, title=f"[bold]{plugin.name}[/bold]", border_style="cyan"))


@cli.group("history")
def history_group() -> None:
    """Inspect or clear the local swarm-test history (.swarmtest-history)."""


@history_group.command("show")
@click.option(
    "--history-dir",
    default=".swarmtest-history",
    show_default=True,
    help="History directory to read from.",
)
@click.option(
    "--swarm",
    default=None,
    help="Filter to a specific swarm name.",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=10,
    show_default=True,
    help="Maximum number of entries to display.",
)
def history_show(history_dir: str, swarm: str | None, limit: int) -> None:
    """Display the trend table of recent runs."""
    from rich.table import Table

    from swarm_test.history import HistoryStore

    store = HistoryStore(history_dir)
    entries = store.load_recent(n=limit, swarm_name=swarm)
    if not entries:
        console.print(
            "[yellow]No swarm-test history found in "
            f"{history_dir}[/yellow]\n"
            "[dim]Run swarm-test on a script first to start tracking trends.[/dim]"
        )
        return

    # Build a oldest → newest series so deltas read forward in time.
    ordered = list(reversed(entries))
    table = Table(title=f"swarm-test history ({len(ordered)} entries)", show_lines=False)
    table.add_column("Timestamp", style="cyan")
    table.add_column("Swarm", style="bold")
    table.add_column("Score", justify="center")
    table.add_column("Findings", justify="center")
    table.add_column("Δ Score", justify="center")

    prior_score: int | None = None
    for entry in ordered:
        score = int(entry.get("swarm_score", 0))
        findings = int(entry.get("total_findings", 0))
        if prior_score is None:
            delta_str = "—"
            delta_style = "dim"
        else:
            diff = score - prior_score
            sign = "+" if diff > 0 else ""
            delta_str = f"{sign}{diff}"
            if diff > 0:
                delta_style = "green"
            elif diff < 0:
                delta_style = "red"
            else:
                delta_style = "dim"
        from rich.text import Text

        table.add_row(
            entry.get("timestamp", "—"),
            entry.get("swarm_name", "—"),
            str(score),
            str(findings),
            Text(delta_str, style=delta_style),
        )
        prior_score = score

    console.print(table)


@history_group.command("clear")
@click.option(
    "--history-dir",
    default=".swarmtest-history",
    show_default=True,
    help="History directory to clear.",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
def history_clear(history_dir: str, yes: bool) -> None:
    """Delete every history snapshot."""
    from swarm_test.history import HistoryStore

    if not yes:
        confirm = click.confirm(
            f"Delete all swarm-test history under {history_dir}?",
            default=False,
        )
        if not confirm:
            console.print("[dim]Aborted — no history was removed.[/dim]")
            return

    store = HistoryStore(history_dir)
    removed = store.clear()
    if removed == 0:
        console.print("[dim]No history files to remove.[/dim]")
    else:
        console.print(f"[green]Removed {removed} history snapshot(s).[/green]")


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


@cli.command("graph")
@click.argument(
    "script",
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    required=False,
)
@click.option(
    "--agents",
    "-a",
    default=None,
    help="Comma-separated agent names (use instead of a script)",
)
@click.option(
    "--edges",
    "-e",
    default=None,
    help="Comma-separated edges: 'A>B' for one-way, 'A<>B' for bidirectional",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["mermaid", "dot", "png"], case_sensitive=False),
    default="mermaid",
    show_default=True,
    help="Export format for the dependency graph",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    help="Output file path (required for png; stdout for mermaid/dot if omitted)",
)
@click.option(
    "--swarm-var",
    default="crew",
    show_default=True,
    help="Variable name of the swarm object in the script",
)
@click.option("--name", default="graph-export", show_default=True, help="Swarm name")
def graph_cmd(
    script: str | None,
    agents: str | None,
    edges: str | None,
    fmt: str,
    output_path: str | None,
    swarm_var: str,
    name: str,
) -> None:
    """Export the agent interaction graph as Mermaid, DOT, or PNG.

    \b
    Examples:
      swarm-test graph --agents "A,B,C" --edges "A>B,B>C" --format mermaid
      swarm-test graph my_crew.py --format png --output graph.png
      swarm-test graph my_crew.py --format dot --output topology.dot
    """
    from swarm_test.core.models import AgentNode, EventType, InteractionEvent
    from swarm_test.core.probe import SwarmProbe
    from swarm_test.reporters import graph_export

    probe_obj: SwarmProbe
    if script:
        import importlib.util

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
        if swarm is None:
            for fallback in ("groupchat", "manager", "group_chat", "swarm", "graph", "agents"):
                candidate = getattr(module, fallback, None)
                if candidate is not None:
                    swarm = candidate
                    break
        probe_obj = SwarmProbe(
            swarm,
            swarm_name=name if name != "graph-export" else Path(script).stem,
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
        )
    else:
        console.print("[red]Provide either a SCRIPT path or both --agents and --edges.[/red]")
        sys.exit(2)

    report = probe_obj.run_all()

    fmt = fmt.lower()
    if fmt == "png":
        if not output_path:
            console.print("[red]--output is required for png format.[/red]")
            sys.exit(2)
        try:
            graph_export.to_png(probe_obj.graph, report=report, output_path=output_path)
        except ImportError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(2)
        console.print(f"[green]PNG graph saved to:[/green] {output_path}")
        return

    text = (
        graph_export.to_mermaid(probe_obj.graph, report=report)
        if fmt == "mermaid"
        else graph_export.to_dot(probe_obj.graph, report=report)
    )
    if output_path:
        with open(output_path, "w") as f:
            f.write(text)
        console.print(f"[green]{fmt.upper()} graph saved to:[/green] {output_path}")
    else:
        console.print(text)


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
