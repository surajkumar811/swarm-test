"""LangGraph framework adapter."""

from __future__ import annotations

import logging
from typing import Any

from swarm_test.core.graph import SwarmGraph
from swarm_test.core.models import AgentNode, EventType, InteractionEvent
from swarm_test.integrations.base import BaseAdapter

logger = logging.getLogger(__name__)

# Sentinel node names used by LangGraph
_START = "__start__"
_END = "__end__"


class LangGraphAdapter(BaseAdapter):
    """
    Adapter for LangGraph ``StateGraph`` and ``CompiledGraph`` objects.

    Ingests:
    - graph.nodes → AgentNode for each node (excluding __start__ / __end__)
    - graph.edges → InteractionEvent for each edge
    - Conditional edges are resolved to their possible target nodes
    """

    framework_name = "langgraph"

    def _ingest_impl(self, swarm: Any, graph: SwarmGraph) -> None:
        # Support both CompiledGraph and StateGraph
        # CompiledGraph wraps a StateGraph; either way we need the node/edge data
        lg_graph = self._unwrap(swarm)

        # -- Extract nodes ---------------------------------------------------
        raw_nodes = self._get_nodes(lg_graph, swarm)
        if not raw_nodes:
            logger.warning("No nodes found in LangGraph object")
            return

        node_map: dict[str, AgentNode] = {}
        for node_name in raw_nodes:
            if node_name in (_START, _END):
                continue
            node = self._make_agent_node(
                name=node_name,
                role=self._infer_role(node_name, raw_nodes.get(node_name)),
                metadata={
                    "langgraph_node": node_name,
                    "has_callable": callable(raw_nodes.get(node_name)),
                },
            )
            graph.add_agent(node)
            node_map[node_name] = node

        if not node_map:
            return

        # -- Extract edges ---------------------------------------------------
        edges = self._get_edges(lg_graph, swarm)
        for src, dst, edge_meta in edges:
            src_name = self._normalize_name(src)
            dst_name = self._normalize_name(dst)

            # Skip edges that only touch START/END without a real counterpart
            if src_name in (_START, _END) and dst_name in (_START, _END):
                continue

            # Map START → first real node, END is a sink
            if src_name == _START:
                if dst_name not in node_map:
                    continue
                # START edges don't create interactions — they define entry points
                node_map[dst_name].metadata["is_entry_point"] = True
                continue

            if dst_name == _END:
                if src_name not in node_map:
                    continue
                node_map[src_name].metadata["is_exit_point"] = True
                continue

            if src_name not in node_map or dst_name not in node_map:
                continue

            event = InteractionEvent(
                source_agent_id=node_map[src_name].id,
                target_agent_id=node_map[dst_name].id,
                event_type=EventType.TASK_DELEGATE,
                payload={
                    "edge_type": edge_meta.get("type", "direct"),
                    "condition": edge_meta.get("condition", ""),
                },
            )
            graph.record_event(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unwrap(swarm: Any) -> Any:
        """Get the underlying graph object from a CompiledGraph or StateGraph."""
        # CompiledGraph stores the builder as .builder
        if hasattr(swarm, "builder"):
            return swarm.builder
        return swarm

    @staticmethod
    def _get_nodes(lg_graph: Any, swarm: Any) -> dict[str, Any]:
        """Extract node name → callable mapping."""
        # StateGraph stores nodes in .nodes dict
        if hasattr(lg_graph, "nodes") and isinstance(lg_graph.nodes, dict):
            return dict(lg_graph.nodes)
        # CompiledGraph may expose nodes directly
        if hasattr(swarm, "nodes") and isinstance(swarm.nodes, dict):
            return dict(swarm.nodes)
        # Fallback: try _nodes
        if hasattr(lg_graph, "_nodes") and isinstance(lg_graph._nodes, dict):
            return dict(lg_graph._nodes)
        return {}

    @staticmethod
    def _get_edges(lg_graph: Any, swarm: Any) -> list[tuple[str, str, dict[str, Any]]]:
        """Extract edges as (source, target, metadata) triples."""
        result: list[tuple[str, str, dict[str, Any]]] = []

        # Try .edges on the graph object
        edges_attr = getattr(lg_graph, "edges", None) or getattr(swarm, "edges", None)

        if edges_attr is not None:
            if isinstance(edges_attr, set):
                for edge in edges_attr:
                    if isinstance(edge, tuple) and len(edge) >= 2:
                        result.append((str(edge[0]), str(edge[1]), {"type": "direct"}))
            elif isinstance(edges_attr, list):
                for edge in edges_attr:
                    if isinstance(edge, tuple) and len(edge) >= 2:
                        meta = edge[2] if len(edge) > 2 and isinstance(edge[2], dict) else {}
                        result.append((str(edge[0]), str(edge[1]), {"type": "direct", **meta}))
                    elif isinstance(edge, dict):
                        result.append(
                            (
                                str(edge.get("source", "")),
                                str(edge.get("target", "")),
                                {"type": "direct"},
                            )
                        )

        # Conditional edges — stored in .branches or ._branches
        branches = getattr(lg_graph, "branches", None) or getattr(lg_graph, "_branches", None)
        if branches and isinstance(branches, dict):
            for src_name, branch_map in branches.items():
                if isinstance(branch_map, dict):
                    for _branch_key, branch in branch_map.items():
                        targets = _extract_branch_targets(branch)
                        for tgt in targets:
                            result.append(
                                (
                                    str(src_name),
                                    str(tgt),
                                    {"type": "conditional", "condition": str(_branch_key)},
                                )
                            )

        # Compiled graph may have a .graph attribute (networkx) with edges
        nx_graph = getattr(swarm, "graph", None)
        if nx_graph is not None and hasattr(nx_graph, "edges"):
            try:
                for u, v, data in nx_graph.edges(data=True):
                    result.append((str(u), str(v), {"type": "compiled", **dict(data)}))
            except Exception:
                pass

        return result

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize START/END sentinel names across LangGraph versions."""
        lower = name.lower().strip()
        if lower in ("__start__", "start", "__input__"):
            return _START
        if lower in ("__end__", "end", "__output__"):
            return _END
        return name

    @staticmethod
    def _infer_role(name: str, callable_obj: Any) -> str:
        """Best-effort role inference from node name or callable."""
        lower = name.lower()
        role_keywords = {
            "research": "researcher",
            "write": "writer",
            "review": "reviewer",
            "edit": "editor",
            "analyz": "analyst",
            "analys": "analyst",
            "plan": "planner",
            "decid": "decision_maker",
            "route": "router",
            "tool": "tool_executor",
            "retriev": "retriever",
            "generat": "generator",
            "summar": "summarizer",
            "validat": "validator",
            "chat": "conversationalist",
            "agent": "agent",
        }
        for keyword, role in role_keywords.items():
            if keyword in lower:
                return role

        # Try docstring of the callable
        if callable_obj and callable(callable_obj):
            doc = getattr(callable_obj, "__doc__", "") or ""
            doc_lower = doc.lower()
            for keyword, role in role_keywords.items():
                if keyword in doc_lower:
                    return role

        return "node"


def _extract_branch_targets(branch: Any) -> list[str]:
    """Extract target node names from a LangGraph Branch object or mapping."""
    targets: list[str] = []

    # Branch object with .ends dict
    ends = getattr(branch, "ends", None)
    if isinstance(ends, dict):
        targets.extend(str(v) for v in ends.values())
        return targets

    # Branch is a dict mapping condition values → target nodes
    if isinstance(branch, dict):
        targets.extend(str(v) for v in branch.values() if isinstance(v, str))
        return targets

    # Branch with .then attribute (default target after conditional)
    then = getattr(branch, "then", None)
    if then and isinstance(then, str):
        targets.append(then)

    return targets
