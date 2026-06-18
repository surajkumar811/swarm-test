"""Dependency graph export — Mermaid, DOT, and PNG renderers."""

from __future__ import annotations

from typing import Any


def _classify_nodes(
    graph: Any,
    report: Any,
) -> dict[str, str]:
    """Map each agent id to one of: 'spof' | 'healthy' | 'moderate'.

    SPOF wins regardless of score. Otherwise we use the redundancy score
    on the report (0 = irreplaceable, 100 = fully redundant):

      >= 60 → healthy
      30-59 → moderate
      <  30 → moderate (rendered as warning; still not a true SPOF)
    """
    spofs = set(graph.find_single_points_of_failure())
    redundancy = getattr(report, "redundancy_scores", {}) or {}

    classes: dict[str, str] = {}
    for nid in graph.graph.nodes():
        if nid in spofs:
            classes[nid] = "spof"
            continue
        score = float(redundancy.get(nid, 0.0))
        if score >= 60:
            classes[nid] = "healthy"
        else:
            classes[nid] = "moderate"
    return classes


def _node_name(graph: Any, nid: str) -> str:
    return graph.graph.nodes[nid].get("name", nid)


def _safe_id(name: str, idx: int) -> str:
    """Return a Mermaid/DOT-safe identifier derived from a node name."""
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"n_{cleaned}" if cleaned else f"n_{idx}"
    return cleaned


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


def to_mermaid(
    graph: Any,
    agents: list[Any] | None = None,
    edges: list[Any] | None = None,
    report: Any = None,
    *,
    direction: str = "TD",
) -> str:
    """Render the swarm graph as Mermaid flowchart syntax.

    Args:
        graph: a SwarmGraph instance.
        agents: optional list of AgentNode (unused; pulled from graph).
        edges: optional list of InteractionEvent (unused; pulled from graph).
        report: optional SwarmReport — used for swarm_score header and
            redundancy-based coloring.
        direction: ``TD`` (top-down) or ``LR`` (left-right).

    Returns:
        A string containing valid Mermaid flowchart syntax.
    """
    g = graph.graph
    classes = _classify_nodes(graph, report) if report is not None else {}
    spofs = set(graph.find_single_points_of_failure())

    # Title comment
    swarm_score = getattr(report, "swarm_score", None) if report is not None else None
    title_bits = ["%% Agent Interaction Graph — swarm-test"]
    if swarm_score is not None:
        title_bits.append(f"%% Swarm Score: {swarm_score}/100")
    title = "\n".join(title_bits)

    lines = [title, f"graph {direction}"]

    # Build stable id mapping
    id_map: dict[str, str] = {}
    seen: set[str] = set()
    for i, nid in enumerate(g.nodes()):
        name = _node_name(graph, nid)
        base = _safe_id(name, i)
        candidate = base
        suffix = 1
        while candidate in seen:
            suffix += 1
            candidate = f"{base}_{suffix}"
        seen.add(candidate)
        id_map[nid] = candidate

    # Nodes
    for nid in g.nodes():
        name = _node_name(graph, nid)
        node_id = id_map[nid]
        if nid in spofs:
            label = f"{name} ⚠️ SPOF"
        else:
            label = name
        cls = classes.get(nid, "moderate")
        lines.append(f"    {node_id}[{label}]:::{cls}")

    # Edges — deduplicate (we only care about presence/direction in static export)
    seen_edges: set[tuple[str, str]] = set()
    for src, dst in g.edges():
        if (src, dst) in seen_edges:
            continue
        seen_edges.add((src, dst))
        lines.append(f"    {id_map[src]} --> {id_map[dst]}")

    # classDef styling
    lines.append("    classDef spof fill:#ff4444,stroke:#cc0000,color:#fff")
    lines.append("    classDef healthy fill:#44cc44,stroke:#22aa22,color:#fff")
    lines.append("    classDef moderate fill:#ffaa00,stroke:#cc8800,color:#fff")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# DOT (Graphviz)
# ---------------------------------------------------------------------------


def to_dot(
    graph: Any,
    agents: list[Any] | None = None,
    edges: list[Any] | None = None,
    report: Any = None,
) -> str:
    """Render the swarm graph as Graphviz DOT format.

    Built as a string (no pydot dependency) so the export works in any
    environment with just networkx installed.
    """
    g = graph.graph
    classes = _classify_nodes(graph, report) if report is not None else {}
    spofs = set(graph.find_single_points_of_failure())
    redundancy = getattr(report, "redundancy_scores", {}) if report is not None else {}
    swarm_score = getattr(report, "swarm_score", None) if report is not None else None

    color_map = {
        "spof": ("#ff4444", "#cc0000"),
        "healthy": ("#44cc44", "#22aa22"),
        "moderate": ("#ffaa00", "#cc8800"),
    }

    title = "Agent Interaction Graph"
    if swarm_score is not None:
        title = f"{title} — Swarm Score: {swarm_score}/100"

    lines: list[str] = []
    lines.append("digraph SwarmTest {")
    lines.append("    rankdir=TB;")
    lines.append(f'    label="{title}";')
    lines.append("    labelloc=t;")
    lines.append('    fontname="Helvetica";')
    lines.append('    node [shape=box, style="filled,rounded", fontname="Helvetica"];')
    lines.append('    edge [fontname="Helvetica"];')

    # Build stable id mapping
    id_map: dict[str, str] = {}
    seen: set[str] = set()
    for i, nid in enumerate(g.nodes()):
        name = _node_name(graph, nid)
        base = _safe_id(name, i)
        candidate = base
        suffix = 1
        while candidate in seen:
            suffix += 1
            candidate = f"{base}_{suffix}"
        seen.add(candidate)
        id_map[nid] = candidate

    # Nodes
    for nid in g.nodes():
        name = _node_name(graph, nid)
        node_id = id_map[nid]
        cls = classes.get(nid, "moderate") if classes else "moderate"
        fill, stroke = color_map[cls]
        score = redundancy.get(nid)
        if score is not None:
            label = f"{name}\\nredundancy: {float(score):.0f}/100"
        else:
            label = name
        if nid in spofs:
            label = f"{label}\\n[SPOF]"
        lines.append(
            f'    {node_id} [label="{label}", '
            f'fillcolor="{fill}", color="{stroke}", fontcolor="white"];'
        )

    # Edges (dedupe)
    seen_edges: set[tuple[str, str]] = set()
    for src, dst in g.edges():
        if (src, dst) in seen_edges:
            continue
        seen_edges.add((src, dst))
        lines.append(f"    {id_map[src]} -> {id_map[dst]};")

    # Legend subgraph
    lines.append("    subgraph cluster_legend {")
    lines.append('        label="Legend";')
    lines.append('        style="dashed";')
    lines.append('        legend_spof [label="SPOF", fillcolor="#ff4444", '
                 'color="#cc0000", fontcolor="white"];')
    lines.append('        legend_moderate [label="Moderate", fillcolor="#ffaa00", '
                 'color="#cc8800", fontcolor="white"];')
    lines.append('        legend_healthy [label="Healthy", fillcolor="#44cc44", '
                 'color="#22aa22", fontcolor="white"];')
    lines.append("    }")

    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# PNG (matplotlib + networkx)
# ---------------------------------------------------------------------------


def to_png(
    graph: Any,
    agents: list[Any] | None = None,
    edges: list[Any] | None = None,
    report: Any = None,
    output_path: str = "swarm_graph.png",
) -> bool:
    """Render the graph to a PNG file via matplotlib + networkx.

    Raises:
        ImportError: if matplotlib is not installed.
    """
    try:
        import matplotlib  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "PNG export requires matplotlib. Install with: pip install swarm-test[png]"
        ) from exc

    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt
    import networkx as nx

    g = graph.graph
    classes = _classify_nodes(graph, report) if report is not None else {}
    swarm_score = getattr(report, "swarm_score", None) if report is not None else None

    color_map = {
        "spof": "#ff4444",
        "healthy": "#44cc44",
        "moderate": "#ffaa00",
    }

    simple_g = nx.DiGraph()
    for nid in g.nodes():
        simple_g.add_node(nid, **g.nodes[nid])
    for src, dst in g.edges():
        simple_g.add_edge(src, dst)

    if simple_g.number_of_nodes() == 0:
        # Save a blank figure rather than fail — caller still gets a file.
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "No agents in graph", ha="center", va="center")
        ax.axis("off")
        fig.savefig(output_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return True

    # Degree-based sizing
    degree = dict(simple_g.degree())
    max_deg = max(degree.values()) if degree else 1
    sizes = [800 + 1500 * (degree[n] / max(max_deg, 1)) for n in simple_g.nodes()]
    colors = [color_map.get(classes.get(n, "moderate"), "#ffaa00") for n in simple_g.nodes()]
    labels = {n: simple_g.nodes[n].get("name", n) for n in simple_g.nodes()}

    # Layout
    try:
        pos = nx.spring_layout(simple_g, seed=42, k=1.2)
    except Exception:
        pos = nx.shell_layout(simple_g)

    fig, ax = plt.subplots(figsize=(11, 8))
    title = "Agent Interaction Graph"
    if swarm_score is not None:
        title = f"{title} — Swarm Score: {swarm_score}/100"
    ax.set_title(title, fontsize=14, fontweight="bold")

    nx.draw_networkx_nodes(
        simple_g, pos, node_color=colors, node_size=sizes,
        edgecolors="#333333", linewidths=1.5, ax=ax,
    )
    nx.draw_networkx_edges(
        simple_g, pos, edge_color="#666666", width=1.2,
        arrows=True, arrowsize=18, arrowstyle="->",
        connectionstyle="arc3,rad=0.08", ax=ax,
    )
    nx.draw_networkx_labels(simple_g, pos, labels=labels, font_size=10, ax=ax)

    legend_handles = [
        mpatches.Patch(color=color_map["spof"], label="SPOF — irreplaceable"),
        mpatches.Patch(color=color_map["moderate"], label="Moderate redundancy"),
        mpatches.Patch(color=color_map["healthy"], label="Healthy / redundant"),
    ]
    ax.legend(handles=legend_handles, loc="lower left", framealpha=0.9)
    ax.axis("off")

    fig.tight_layout()
    fig.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# Aggregate helper for CLI use
# ---------------------------------------------------------------------------


def export(
    fmt: str,
    graph: Any,
    *,
    report: Any = None,
    output_path: str | None = None,
) -> str | bool:
    """Dispatch a single-call export. Returns the rendered string for
    mermaid/dot, or ``True`` for png."""
    agents = list(graph.agents.values()) if hasattr(graph, "agents") else []
    events = list(graph.events) if hasattr(graph, "events") else []
    fmt = fmt.lower()
    if fmt == "mermaid":
        return to_mermaid(graph, agents, events, report)
    if fmt == "dot":
        return to_dot(graph, agents, events, report)
    if fmt == "png":
        if not output_path:
            raise ValueError("PNG export requires an output_path")
        return to_png(graph, agents, events, report, output_path)
    raise ValueError(f"Unknown export format: {fmt}")


__all__ = ["to_mermaid", "to_dot", "to_png", "export"]
