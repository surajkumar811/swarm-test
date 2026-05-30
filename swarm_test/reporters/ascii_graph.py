"""ASCII agent interaction graph renderer using Rich and box-drawing characters."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import networkx as nx
from rich.console import Console


def _score_style(score: int) -> str:
    """Return Rich markup color tag for a health score."""
    if score >= 60:
        return "green"
    if score >= 30:
        return "yellow"
    return "red"


class AsciiGraphRenderer:
    """Renders the agent interaction graph as a wire-diagram in the terminal.

    Produces output like::

        Agent Interaction Graph
        ━━━━━━━━━━━━━━━━━━━━━━

        [Researcher 36/100] ──→ [Analyst 60/100] ──→ [Writer 56/100]
              ↑                                           │
              └──────────── [Reviewer 52/100] ←───────────┘

        ⚠  SPOFs: Researcher
        ↻  Cycles: Writer → Reviewer → Writer
        ⟿  Critical Path: Researcher → Analyst → Writer → Reviewer (4 hops)
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)

    def render(
        self,
        graph: Any,
        agent_scores: dict[str, Any] | None = None,
    ) -> None:
        """Print the agent interaction graph to the terminal."""
        c = self.console
        g: nx.MultiDiGraph = graph.graph
        agent_scores = agent_scores or {}

        if g.number_of_nodes() == 0:
            c.print("[yellow]No agents in graph.[/yellow]")
            return

        # -- Precompute analysis ----------------------------------------
        spofs = set(graph.find_single_points_of_failure())
        cycles = graph.find_cycles()
        critical_path = graph.get_critical_path()

        blast_radius: dict[str, float] = {}
        for nid in g.nodes:
            br = graph.get_blast_radius(nid)
            blast_radius[nid] = br["impact_percentage"]

        # Edge map: (src, dst) -> count
        edge_count: dict[tuple[str, str], int] = defaultdict(int)
        for src, dst, _data in g.edges(data=True):
            edge_count[(src, dst)] += 1

        bidir: set[frozenset[str]] = set()
        for src, dst in edge_count:
            if (dst, src) in edge_count:
                bidir.add(frozenset([src, dst]))

        # -- Header -----------------------------------------------------
        c.print()
        c.print("  [bold blue]Agent Interaction Graph[/bold blue]")
        c.print("  [blue]━━━━━━━━━━━━━━━━━━━━━━[/blue]")
        c.print()

        # -- Determine layout order -------------------------------------
        if critical_path and len(critical_path) > 1:
            main_row = list(critical_path)
        else:
            simple_g = nx.DiGraph(g)
            try:
                main_row = list(nx.topological_sort(simple_g))
            except nx.NetworkXUnfeasible:
                main_row = list(g.nodes)

        main_set = set(main_row)
        extra_nodes = [n for n in g.nodes if n not in main_set]

        # -- Helper: build node label -----------------------------------
        def _label(nid: str) -> str:
            name = g.nodes[nid].get("name", nid)
            score_obj = agent_scores.get(nid)
            parts = [name]
            if score_obj is not None:
                parts.append(f"{score_obj.score}/100")
            br_val = blast_radius.get(nid, 0.0)
            parts.append(f"{br_val:.0f}%")
            if nid in spofs:
                parts.append("⚠")
            return " ".join(parts)

        def _markup(nid: str) -> str:
            score_obj = agent_scores.get(nid)
            score = score_obj.score if score_obj else None
            color = _score_style(score) if score is not None else "white"
            return f"[{color}]\\[{_label(nid)}][/{color}]"

        # -- Draw main pipeline -----------------------------------------
        # Row 1: nodes connected by arrows
        row_parts: list[str] = []
        plain_parts: list[str] = []  # for width calculations
        node_centers: dict[str, int] = {}
        pos = 0

        for i, nid in enumerate(main_row):
            label = f"[{_label(nid)}]"
            node_centers[nid] = pos + len(label) // 2
            row_parts.append(_markup(nid))
            plain_parts.append(label)
            pos += len(label)

            if i < len(main_row) - 1:
                next_nid = main_row[i + 1]
                pair = frozenset([nid, next_nid])
                if pair in bidir:
                    arr_mk = " [yellow]↔[/yellow] "
                    arr_pl = " ↔ "
                else:
                    arr_mk = " [green]──→[/green] "
                    arr_pl = " ──→ "
                row_parts.append(arr_mk)
                plain_parts.append(arr_pl)
                pos += len(arr_pl)

        c.print(f"  {''.join(row_parts)}")

        # -- Find back-edges (not on main pipeline) --------------------
        main_edges: set[tuple[str, str]] = set()
        for i in range(len(main_row) - 1):
            main_edges.add((main_row[i], main_row[i + 1]))

        back_edges: list[tuple[str, str]] = []
        for (src, dst) in edge_count:
            if (src, dst) in main_edges:
                continue
            if (dst, src) in main_edges and frozenset([src, dst]) in bidir:
                continue
            back_edges.append((src, dst))

        # -- Draw back-edge loops below the main row -------------------
        for src, dst in back_edges:
            src_name = g.nodes[src].get("name", src)
            dst_name = g.nodes[dst].get("name", dst)

            if src in node_centers and dst in node_centers:
                # Both on main row — draw a U-shaped connector
                left_pos = min(node_centers[src], node_centers[dst])
                right_pos = max(node_centers[src], node_centers[dst])
                # Vertical ticks
                vert_line = [" "] * (right_pos + 3)
                vert_line[left_pos + 2] = "│"
                vert_line[right_pos + 2] = "│"
                c.print(f"  [dim]{''.join(vert_line)}[/dim]")
                # Horizontal connector with label
                conn = [" "] * (left_pos + 2)
                conn.append("└")
                mid_width = max(0, right_pos - left_pos - 1)
                # Place label in the middle of the connector
                label = f" {src_name} → {dst_name} "
                if mid_width > len(label) + 2:
                    pad_left = (mid_width - len(label)) // 2
                    pad_right = mid_width - len(label) - pad_left
                    conn_str = "─" * pad_left + label + "─" * pad_right
                else:
                    conn_str = "─" * mid_width
                conn.append(conn_str)
                conn.append("┘")
                c.print(f"  [cyan]{''.join(conn)}[/cyan]")
            else:
                # One or both off the main row
                c.print(f"  [cyan]  └── {src_name} → {dst_name}[/cyan]")

        # -- Extra nodes -----------------------------------------------
        if extra_nodes:
            c.print()
            extras = [_markup(nid) for nid in extra_nodes]
            c.print(f"  [dim]Other agents:[/dim] {', '.join(extras)}")

        c.print()

        # -- Summary ---------------------------------------------------
        if spofs:
            names = [g.nodes[s].get("name", s) for s in spofs]
            c.print(
                f"  [bold red]⚠  SPOFs:[/bold red] "
                f"[red]{', '.join(names)}[/red]"
            )
        else:
            c.print("  [green]⚠  SPOFs:[/green] [dim]none[/dim]")

        if cycles:
            strs = []
            for cy in cycles[:5]:
                ns = [g.nodes[n].get("name", n) for n in cy]
                strs.append(" → ".join(ns) + " → " + ns[0])
            c.print(
                f"  [yellow]↻  Cycles ({len(cycles)}):[/yellow] "
                + " | ".join(strs)
            )
        else:
            c.print("  [green]↻  Cycles:[/green] [dim]none[/dim]")

        if critical_path:
            ns = [g.nodes[n].get("name", n) for n in critical_path]
            c.print(
                f"  [cyan]⟿  Critical Path "
                f"({len(critical_path)} hops):[/cyan] "
                + " → ".join(ns)
            )

        # -- Legend ----------------------------------------------------
        c.print()
        c.print(
            "  [dim]Legend: "
            "[green]──→[/green] one-way  "
            "[cyan]←──[/cyan] reverse  "
            "[yellow]↔[/yellow] bidirectional  "
            "[red]RED[/red] <30  "
            "[yellow]YELLOW[/yellow] 30-60  "
            "[green]GREEN[/green] 60+  "
            "[red]⚠[/red] SPOF[/dim]"
        )
        c.print()
