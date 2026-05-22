"""Base adapter interface for framework integrations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from swarm_test.core.graph import SwarmGraph
from swarm_test.core.models import AgentNode, EventType, InteractionEvent

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """
    Base class for all framework adapters.

    Adapters are responsible for introspecting a specific agent framework's
    objects (Crew, GroupChat, StateGraph, etc.) and populating a SwarmGraph
    with the corresponding agent nodes and relationships.
    """

    framework_name: str = "generic"

    def ingest(self, swarm: Any, graph: SwarmGraph) -> None:
        """
        Entry point — inspect the swarm object and populate the graph.
        Falls back to generic object inspection if framework-specific
        ingestion is not possible.
        """
        try:
            self._ingest_impl(swarm, graph)
        except Exception as exc:
            logger.warning(
                "[%s] Framework-specific ingestion failed: %s. Falling back to generic.",
                self.framework_name,
                exc,
            )
            self._generic_ingest(swarm, graph)

    def _ingest_impl(self, swarm: Any, graph: SwarmGraph) -> None:
        """Override in subclasses for framework-specific ingestion."""
        self._generic_ingest(swarm, graph)

    @staticmethod
    def _generic_ingest(swarm: Any, graph: SwarmGraph) -> None:
        """
        Fallback: try to find agents via common attribute names and
        build a linear chain of interaction events as a minimal graph.
        """
        agents_list = None
        for attr in ("agents", "members", "workers", "participants", "_agents"):
            agents_list = getattr(swarm, attr, None)
            if agents_list:
                break

        if not agents_list:
            # Last resort: treat the swarm itself as a single agent node
            node = AgentNode(
                name=getattr(swarm, "name", type(swarm).__name__),
                role="unknown",
                framework="generic",
            )
            graph.add_agent(node)
            return

        nodes = []
        for raw_agent in agents_list:
            name = (
                getattr(raw_agent, "name", None)
                or getattr(raw_agent, "role", None)
                or type(raw_agent).__name__
            )
            role = getattr(raw_agent, "role", "unknown") or "unknown"
            node = AgentNode(
                name=str(name),
                role=str(role),
                framework="generic",
                metadata={"original_type": type(raw_agent).__name__},
            )
            graph.add_agent(node)
            nodes.append(node)

        # Build a linear chain as a minimal interaction graph
        for i in range(len(nodes) - 1):
            event = InteractionEvent(
                source_agent_id=nodes[i].id,
                target_agent_id=nodes[i + 1].id,
                event_type=EventType.TASK_DELEGATE,
                payload={"inferred": True},
            )
            graph.record_event(event)

    def _make_agent_node(
        self,
        name: str,
        role: str = "unknown",
        metadata: Optional[dict] = None,
    ) -> AgentNode:
        return AgentNode(
            name=name,
            role=role,
            framework=self.framework_name,
            metadata=metadata or {},
        )
