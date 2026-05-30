"""ASCII agent interaction graph renderer using Rich."""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class AsciiGraphRenderer:
    """Renders agent interaction topology as a Rich table with graph annotations."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)

    def render(
        self,
        graph: Any,
        agent_scores: dict[str, Any] | None = None,
    ) -> None:
        """Print the agent interaction graph to the terminal.

        Args:
            graph: A ``SwarmGraph`` instance.
            agent_scores: Optional dict of agent_id -> AgentHealthScore.
        """
        c = self.console
        g = graph.graph  # The underlying NetworkX MultiDiGraph
        agent_scores = agent_scores or {}

        if g.number_of_nodes() == 0:
            c.print("[yellow]No agents in graph.[/yellow]")
            return

        # Precompute graph analysis
        spofs = set(graph.find_single_points_of_failure())
        cycles = graph.find_cycles()
        critical_path = graph.get_critical_path()

        # Build blast radius lookup
        blast_radius: dict[str, float] = {}
        for nid in g.nodes:
            br = graph.get_blast_radius(nid)
            blast_radius[nid] = br["impact_percentage"]

        # Build edge map: (src, dst) -> list of event types
        edge_map: dict[tuple[str, str], list[str]] = {}
        for src, dst, data in g.edges(data=True):
            key = (src, dst)
            etype = data.get("event_type", "?")
            edge_map.setdefault(key, []).append(etype)

        # Bidirectional detection
        bidir_pairs: set[frozenset[str]] = set()
        for src, dst in edge_map:
            if (dst, src) in edge_map:
                bidir_pairs.add(frozenset([src, dst]))

        # Header
        c.print()
        c.print(Panel(
            "[bold]Agent Interaction Graph[/bold]",
            border_style="blue",
            expand=False,
        ))
        c.print()

        # Node table
        node_table = Table(
            title="Agents",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        node_table.add_column("Agent", style="bold", width=24)
        node_table.add_column("Role", width=14)
        node_table.add_column("Health", width=10, justify="center")
        node_table.add_column("Blast %", width=10, justify="center")
        node_table.add_column("Flags", width=12)

        for nid, data in g.nodes(data=True):
            name = data.get("name", nid)
            role = data.get("role", "unknown")

            # Health score
            score_obj = agent_scores.get(nid)
            if score_obj is not None:
                score = score_obj.score
                if score >= 60:
                    health_text = Text(f"{score}/100", style="green")
                elif score >= 30:
                    health_text = Text(f"{score}/100", style="yellow")
                else:
                    health_text = Text(f"{score}/100", style="bold red")
            else:
                health_text = Text("-", style="dim")

            # Blast radius
            br_val = blast_radius.get(nid, 0.0)
            if br_val >= 50:
                br_text = Text(f"{br_val:.0f}%", style="bold red")
            elif br_val >= 25:
                br_text = Text(f"{br_val:.0f}%", style="yellow")
            else:
                br_text = Text(f"{br_val:.0f}%", style="green")

            # Flags
            flags = []
            if nid in spofs:
                flags.append("SPOF")
            if g.in_degree(nid) == 0:
                flags.append("ROOT")
            if g.out_degree(nid) == 0:
                flags.append("LEAF")
            flags_text = Text(", ".join(flags), style="red" if "SPOF" in flags else "dim")

            node_table.add_row(name, role, health_text, br_text, flags_text)

        c.print(node_table)
        c.print()

        # Edge table with arrows
        edge_table = Table(
            title="Edges",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        edge_table.add_column("Source", style="bold", width=20)
        edge_table.add_column("", width=5, justify="center")
        edge_table.add_column("Target", style="bold", width=20)
        edge_table.add_column("Type", width=16)
        edge_table.add_column("Count", width=8, justify="center")

        shown_bidir: set[frozenset[str]] = set()
        for (src, dst), etypes in sorted(edge_map.items()):
            src_name = g.nodes[src].get("name", src)
            dst_name = g.nodes[dst].get("name", dst)
            pair = frozenset([src, dst])

            if pair in bidir_pairs:
                if pair in shown_bidir:
                    continue
                shown_bidir.add(pair)
                arrow = Text("<->", style="bold yellow")
            else:
                arrow = Text("-->", style="bold green")

            # Deduplicate event types
            unique_types = sorted(set(etypes))
            type_str = ", ".join(unique_types)
            total_count = len(etypes)
            if pair in bidir_pairs:
                reverse_types = edge_map.get((dst, src), [])
                total_count += len(reverse_types)
                unique_types = sorted(set(etypes + reverse_types))
                type_str = ", ".join(unique_types)

            edge_table.add_row(src_name, arrow, dst_name, type_str, str(total_count))

        c.print(edge_table)
        c.print()

        # Summary panel
        summary_lines = []

        # SPOFs
        if spofs:
            spof_names = [g.nodes[s].get("name", s) for s in spofs]
            summary_lines.append(f"[bold red]SPOFs:[/bold red] {', '.join(spof_names)}")
        else:
            summary_lines.append("[green]SPOFs:[/green] none")

        # Cycles
        if cycles:
            cycle_strs = []
            for cycle in cycles[:5]:  # Show max 5 cycles
                names = [g.nodes[n].get("name", n) for n in cycle]
                cycle_strs.append(" -> ".join(names) + " -> " + names[0])
            cycle_list = " | ".join(cycle_strs)
            summary_lines.append(f"[yellow]Cycles ({len(cycles)}):[/yellow] {cycle_list}")
        else:
            summary_lines.append("[green]Cycles:[/green] none")

        # Critical path
        if critical_path:
            cp_names = [g.nodes[n].get("name", n) for n in critical_path]
            summary_lines.append(
                f"[cyan]Critical Path ({len(critical_path)} hops):[/cyan] "
                + " -> ".join(cp_names)
            )

        c.print(Panel(
            "\n".join(summary_lines),
            title="[bold]Topology Summary[/bold]",
            border_style="blue",
        ))

        # Legend
        c.print()
        c.print(
            "[dim]Legend: "
            "[green]-->  one-way edge[/green]  "
            "[yellow]<->  bidirectional[/yellow]  "
            "[red]SPOF  single point of failure[/red]  "
            "ROOT  no incoming  "
            "LEAF  no outgoing[/dim]"
        )
        c.print()
