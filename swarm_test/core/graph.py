"""NetworkX-based interaction graph for agent swarms."""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from swarm_test.core.models import AgentNode, InteractionEvent

logger = logging.getLogger(__name__)


class SwarmGraph:
    """Directed multigraph tracking all agent interactions."""

    def __init__(self) -> None:
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._agents: dict[str, AgentNode] = {}
        self._events: list[InteractionEvent] = []

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def add_agent(self, agent: AgentNode) -> None:
        """Register an agent node in the graph."""
        self._agents[agent.id] = agent
        self._graph.add_node(
            agent.id,
            name=agent.name,
            role=agent.role,
            framework=agent.framework,
            metadata=agent.metadata,
        )
        logger.debug("Added agent node: %s (%s)", agent.name, agent.id)

    def record_event(self, event: InteractionEvent) -> None:
        """Record an interaction event and add the corresponding graph edge."""
        self._events.append(event)

        # Auto-create missing nodes
        for aid in (event.source_agent_id, event.target_agent_id):
            if aid not in self._graph:
                self._graph.add_node(aid, name=aid, role="unknown", framework="unknown")

        self._graph.add_edge(
            event.source_agent_id,
            event.target_agent_id,
            key=event.id,
            event_type=event.event_type.value,
            timestamp=event.timestamp,
            success=event.success,
            duration_ms=event.duration_ms,
            payload=event.payload,
        )

    # ------------------------------------------------------------------
    # Graph queries
    # ------------------------------------------------------------------

    def get_downstream(self, agent_id: str) -> list[str]:
        """Return all agents reachable from agent_id (downstream)."""
        if agent_id not in self._graph:
            return []
        return list(nx.descendants(self._graph, agent_id))

    def get_upstream(self, agent_id: str) -> list[str]:
        """Return all agents that can reach agent_id (upstream)."""
        if agent_id not in self._graph:
            return []
        return list(nx.ancestors(self._graph, agent_id))

    def get_blast_radius(self, agent_id: str) -> dict[str, Any]:
        """
        Calculate the blast radius if agent_id fails.
        Returns affected agents, edges, and impact percentage.
        """
        downstream = self.get_downstream(agent_id)
        total_agents = self._graph.number_of_nodes()
        downstream_set = set(downstream)
        affected_edges = []

        for src, dst, data in self._graph.edges(data=True):
            if src == agent_id or src in downstream_set:
                affected_edges.append((src, dst, data))

        impact_pct = (len(downstream) / max(total_agents - 1, 1)) * 100

        agent_name = agent_id
        if agent_id in self._graph:
            agent_name = self._graph.nodes[agent_id].get("name", agent_id)

        return {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "downstream_agents": downstream,
            "affected_edge_count": len(affected_edges),
            "total_agents": total_agents,
            "impact_percentage": round(impact_pct, 2),
        }

    def find_single_points_of_failure(self) -> list[str]:
        """
        Identify agents whose removal would disconnect the graph.
        Uses articulation points on the underlying undirected graph.
        """
        undirected = self._graph.to_undirected()
        try:
            spofs = list(nx.articulation_points(undirected))
        except nx.NetworkXError:
            spofs = []
        return spofs

    def find_cycles(self) -> list[list[str]]:
        """Return all simple cycles in the directed interaction graph."""
        try:
            cycles = list(nx.simple_cycles(self._graph))
        except nx.NetworkXError:
            cycles = []
        return cycles

    def get_critical_path(self, source: str | None = None, target: str | None = None) -> list[str]:
        """
        Return the longest path (critical path) through the DAG.
        If source/target not given, uses dag_longest_path.
        Falls back gracefully when cycles exist.
        """
        dag = nx.DiGraph(self._graph)  # Simple digraph (no multi-edges)
        # Attempt with topological sort; abort on cycles
        try:
            if source and target:
                if nx.has_path(dag, source, target):
                    all_paths = list(nx.all_simple_paths(dag, source, target))
                    if all_paths:
                        return max(all_paths, key=len)
                return []
            return nx.dag_longest_path(dag)
        except nx.NetworkXUnfeasible:
            # Graph has cycles — return the longest shortest path instead
            try:
                lengths = dict(nx.all_pairs_shortest_path_length(dag))
                best: tuple[int, list[str]] = (0, [])
                for src in lengths:
                    for dst, length in lengths[src].items():
                        if length > best[0]:
                            path = nx.shortest_path(dag, src, dst)
                            best = (length, path)
                return best[1]
            except Exception:
                return []

    def get_centrality(self) -> dict[str, float]:
        """Return betweenness centrality for all nodes."""
        if self._graph.number_of_nodes() == 0:
            return {}
        return nx.betweenness_centrality(self._graph)

    def get_in_degree(self) -> dict[str, int]:
        return dict(self._graph.in_degree())

    def get_out_degree(self) -> dict[str, int]:
        return dict(self._graph.out_degree())

    def summary_metrics(self) -> dict[str, Any]:
        """Return a dict of graph-level metrics."""
        g = self._graph
        n = g.number_of_nodes()
        e = g.number_of_edges()
        cycles = self.find_cycles()
        spofs = self.find_single_points_of_failure()
        centrality = self.get_centrality()

        return {
            "node_count": n,
            "edge_count": e,
            "cycle_count": len(cycles),
            "single_points_of_failure": len(spofs),
            "density": round(nx.density(g), 4),
            "is_weakly_connected": nx.is_weakly_connected(g) if n > 0 else False,
            "top_central_agent": (
                max(centrality, key=lambda k: centrality[k]) if centrality else None
            ),
            "max_betweenness": round(max(centrality.values()), 4) if centrality else 0,
            "critical_path_length": len(self.get_critical_path()),
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def agents(self) -> dict[str, AgentNode]:
        return self._agents

    @property
    def events(self) -> list[InteractionEvent]:
        return self._events

    @property
    def graph(self) -> nx.MultiDiGraph:
        return self._graph

    def node_data(self) -> list[dict[str, Any]]:
        """Serialize nodes for rendering."""
        nodes = []
        for nid, data in self._graph.nodes(data=True):
            nodes.append({"id": nid, **data})
        return nodes

    def edge_data(self) -> list[dict[str, Any]]:
        """Serialize edges for rendering."""
        edges = []
        for src, dst, key, data in self._graph.edges(keys=True, data=True):
            edges.append({"source": src, "target": dst, "key": key, **data})
        return edges
