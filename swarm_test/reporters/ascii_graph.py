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
    """Renders the agent interaction graph as a wire-diagram in the terminal."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        # Edge map
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

        # -- Choose layout strategy -------------------------------------
        n_nodes = g.number_of_nodes()

        # Detect hub-and-spoke: find node with highest total degree
        hub_id = max(g.nodes, key=lambda n: g.degree(n))
        hub_degree = g.degree(hub_id)

        if n_nodes > 5 and hub_degree >= n_nodes - 1:
            self._render_hub_spoke(
                c, g, hub_id, agent_scores, blast_radius,
                spofs, edge_count, bidir,
            )
        else:
            self._render_pipeline(
                c, g, agent_scores, blast_radius,
                spofs, edge_count, bidir, critical_path,
            )

        c.print()

        # -- Summary ---------------------------------------------------
        self._render_summary(c, g, spofs, cycles, critical_path)

        # -- Legend ----------------------------------------------------
        c.print()
        c.print(
            "  [dim]Legend: "
            "[green]──→[/green] one-way  "
            "[yellow]↔[/yellow] bidirectional  "
            "[red]RED[/red] <30  "
            "[yellow]YELLOW[/yellow] 30-60  "
            "[green]GREEN[/green] 60+  "
            "[red]⚠[/red] SPOF[/dim]"
        )
        c.print()

    # ------------------------------------------------------------------
    # Node label helpers
    # ------------------------------------------------------------------

    def _label(self, g, nid, agent_scores, blast_radius, spofs):
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

    def _markup(self, g, nid, agent_scores, blast_radius, spofs):
        score_obj = agent_scores.get(nid)
        score = score_obj.score if score_obj else None
        color = _score_style(score) if score is not None else "white"
        label = self._label(g, nid, agent_scores, blast_radius, spofs)
        return f"[{color}]\\[{label}][/{color}]"

    def _name(self, g, nid):
        return g.nodes[nid].get("name", nid)

    # ------------------------------------------------------------------
    # Hub-and-spoke layout (for star topologies like ARE)
    # ------------------------------------------------------------------

    def _render_hub_spoke(self, c, g, hub_id, agent_scores,
                          blast_radius, spofs, edge_count, bidir):
        """Render a star topology with the hub in the center."""
        hub_mk = self._markup(g, hub_id, agent_scores, blast_radius, spofs)

        # Classify spokes by direction
        outbound = []   # hub -> spoke (one-way)
        inbound = []    # spoke -> hub (one-way)
        both_dirs = []  # hub <-> spoke

        all_spokes = set()
        for nid in g.nodes:
            if nid == hub_id:
                continue
            has_out = (hub_id, nid) in edge_count
            has_in = (nid, hub_id) in edge_count
            if has_out and has_in:
                both_dirs.append(nid)
            elif has_out:
                outbound.append(nid)
            elif has_in:
                inbound.append(nid)
            all_spokes.add(nid)

        # Find non-hub edges (spoke-to-spoke)
        cross_edges = []
        for (src, dst) in edge_count:
            if src != hub_id and dst != hub_id:
                cross_edges.append((src, dst))

        # Print the hub
        c.print(f"  {hub_mk}")
        c.print(f"  [dim]{'─' * 40}[/dim]")

        # Bidirectional spokes (most common in hub-and-spoke)
        if both_dirs:
            c.print("  [dim]  ↔  bidirectional:[/dim]")
            for nid in both_dirs:
                mk = self._markup(g, nid, agent_scores, blast_radius, spofs)
                cnt = edge_count.get((hub_id, nid), 0)
                cnt += edge_count.get((nid, hub_id), 0)
                c.print(
                    f"  [yellow]    ↔[/yellow]  {mk}"
                    f"  [dim]({cnt} events)[/dim]"
                )

        # Outbound only
        if outbound:
            c.print("  [dim]  ──→  outbound only:[/dim]")
            for nid in outbound:
                mk = self._markup(g, nid, agent_scores, blast_radius, spofs)
                cnt = edge_count.get((hub_id, nid), 0)
                c.print(
                    f"  [green]    ──→[/green]  {mk}"
                    f"  [dim]({cnt} events)[/dim]"
                )

        # Inbound only
        if inbound:
            c.print("  [dim]  ←──  inbound only:[/dim]")
            for nid in inbound:
                mk = self._markup(g, nid, agent_scores, blast_radius, spofs)
                cnt = edge_count.get((nid, hub_id), 0)
                c.print(
                    f"  [cyan]    ←──[/cyan]  {mk}"
                    f"  [dim]({cnt} events)[/dim]"
                )

        # Cross-edges (spoke-to-spoke, bypassing hub)
        if cross_edges:
            c.print()
            c.print("  [dim]  cross-links (bypass hub):[/dim]")
            shown = set()
            for src, dst in cross_edges:
                pair = frozenset([src, dst])
                if pair in shown:
                    continue
                src_name = self._name(g, src)
                dst_name = self._name(g, dst)
                if pair in bidir:
                    shown.add(pair)
                    c.print(
                        f"  [yellow]    {src_name} ↔ {dst_name}[/yellow]"
                    )
                else:
                    c.print(
                        f"  [cyan]    {src_name} ──→ {dst_name}[/cyan]"
                    )

    # ------------------------------------------------------------------
    # Pipeline layout (for linear / DAG topologies)
    # ------------------------------------------------------------------

    def _render_pipeline(self, c, g, agent_scores, blast_radius,
                         spofs, edge_count, bidir, critical_path):
        """Render a linear pipeline based on critical path / topo sort."""
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

        # Build main row with arrows
        row_parts = []
        plain_len = 0
        node_centers: dict[str, int] = {}

        for i, nid in enumerate(main_row):
            label = f"[{self._label(g, nid, agent_scores, blast_radius, spofs)}]"
            node_centers[nid] = plain_len + len(label) // 2
            row_parts.append(
                self._markup(g, nid, agent_scores, blast_radius, spofs)
            )
            plain_len += len(label)

            if i < len(main_row) - 1:
                next_nid = main_row[i + 1]
                pair = frozenset([nid, next_nid])
                if pair in bidir:
                    row_parts.append(" [yellow]↔[/yellow] ")
                else:
                    row_parts.append(" [green]──→[/green] ")
                plain_len += 5  # " ──→ "

        c.print(f"  {''.join(row_parts)}")

        # Back-edges
        main_edges = set()
        for i in range(len(main_row) - 1):
            main_edges.add((main_row[i], main_row[i + 1]))

        back_edges = []
        for (src, dst) in edge_count:
            if (src, dst) in main_edges:
                continue
            if (dst, src) in main_edges and frozenset([src, dst]) in bidir:
                continue
            back_edges.append((src, dst))

        for src, dst in back_edges:
            src_name = self._name(g, src)
            dst_name = self._name(g, dst)

            if src in node_centers and dst in node_centers:
                left = min(node_centers[src], node_centers[dst])
                right = max(node_centers[src], node_centers[dst])
                vert = [" "] * (right + 3)
                vert[left + 2] = "│"
                vert[right + 2] = "│"
                c.print(f"  [dim]{''.join(vert)}[/dim]")
                conn = " " * (left + 2) + "└"
                mid = max(0, right - left - 1)
                label = f" {src_name} → {dst_name} "
                if mid > len(label) + 2:
                    pl = (mid - len(label)) // 2
                    pr = mid - len(label) - pl
                    conn += "─" * pl + label + "─" * pr
                else:
                    conn += "─" * mid
                conn += "┘"
                c.print(f"  [cyan]{conn}[/cyan]")
            else:
                c.print(f"  [cyan]  └── {src_name} → {dst_name}[/cyan]")

        if extra_nodes:
            c.print()
            extras = [
                self._markup(g, n, agent_scores, blast_radius, spofs)
                for n in extra_nodes
            ]
            c.print(f"  [dim]Other agents:[/dim] {', '.join(extras)}")

    # ------------------------------------------------------------------
    # Summary section
    # ------------------------------------------------------------------

    def _render_summary(self, c, g, spofs, cycles, critical_path):
        if spofs:
            names = [self._name(g, s) for s in spofs]
            c.print(
                f"  [bold red]⚠  SPOFs:[/bold red] "
                f"[red]{', '.join(names)}[/red]"
            )
        else:
            c.print("  [green]⚠  SPOFs:[/green] [dim]none[/dim]")

        if cycles:
            strs = []
            for cy in cycles[:5]:
                ns = [self._name(g, n) for n in cy]
                strs.append(" → ".join(ns) + " → " + ns[0])
            c.print(
                f"  [yellow]↻  Cycles ({len(cycles)}):[/yellow] "
                + " | ".join(strs)
            )
        else:
            c.print("  [green]↻  Cycles:[/green] [dim]none[/dim]")

        if critical_path:
            ns = [self._name(g, n) for n in critical_path]
            c.print(
                f"  [cyan]⟿  Critical Path "
                f"({len(critical_path)} hops):[/cyan] "
                + " → ".join(ns)
            )
