"""CrewAI framework adapter."""

from __future__ import annotations

import logging
from typing import Any

from swarm_test.core.graph import SwarmGraph
from swarm_test.core.models import AgentNode, EventType, InteractionEvent
from swarm_test.integrations.base import BaseAdapter

logger = logging.getLogger(__name__)


class CrewAIAdapter(BaseAdapter):
    """
    Adapter for CrewAI Crew objects.

    Ingests:
    - crew.agents → AgentNode for each agent
    - crew.tasks → infers delegation edges based on task dependencies
    - crew.process → linear (sequential) or parallel topology
    """

    framework_name = "crewai"

    def _ingest_impl(self, swarm: Any, graph: SwarmGraph) -> None:
        agents_raw: list[Any] = getattr(swarm, "agents", []) or []
        tasks_raw: list[Any] = getattr(swarm, "tasks", []) or []
        process = getattr(swarm, "process", "sequential")

        # Map raw agent → AgentNode
        agent_map: dict[int, AgentNode] = {}
        for raw_agent in agents_raw:
            node = self._build_node(raw_agent)
            graph.add_agent(node)
            agent_map[id(raw_agent)] = node
            logger.debug("CrewAI agent ingested: %s (%s)", node.name, node.id)

        if not agent_map:
            logger.warning("No agents found in Crew object")
            return

        nodes = list(agent_map.values())

        # Build task-level dependency edges
        task_agent_map: dict[int, AgentNode] = {}
        for task in tasks_raw:
            raw_agent = getattr(task, "agent", None)
            if raw_agent is not None and id(raw_agent) in agent_map:
                task_agent_map[id(task)] = agent_map[id(raw_agent)]

        # Wire up context dependencies between tasks
        for task in tasks_raw:
            context_tasks = getattr(task, "context", None) or []
            dst_node = task_agent_map.get(id(task))
            if dst_node is None:
                continue
            for ctx_task in context_tasks:
                src_node = task_agent_map.get(id(ctx_task))
                if src_node and src_node.id != dst_node.id:
                    event = InteractionEvent(
                        source_agent_id=src_node.id,
                        target_agent_id=dst_node.id,
                        event_type=EventType.CONTEXT_SHARE,
                        payload={
                            "task_name": getattr(task, "description", "")[:100],
                            "context_task": getattr(ctx_task, "description", "")[:100],
                        },
                    )
                    graph.record_event(event)

        # Fall back to sequential chain if no context edges were created
        if graph.graph.number_of_edges() == 0:
            process_str = str(process).lower()
            if "sequential" in process_str:
                for i in range(len(nodes) - 1):
                    event = InteractionEvent(
                        source_agent_id=nodes[i].id,
                        target_agent_id=nodes[i + 1].id,
                        event_type=EventType.TASK_DELEGATE,
                        payload={"process": "sequential", "inferred": True},
                    )
                    graph.record_event(event)
            elif "hierarchical" in process_str and len(nodes) >= 2:
                # Manager (first agent) delegates to all others
                manager = nodes[0]
                for worker in nodes[1:]:
                    event = InteractionEvent(
                        source_agent_id=manager.id,
                        target_agent_id=worker.id,
                        event_type=EventType.TASK_DELEGATE,
                        payload={"process": "hierarchical", "inferred": True},
                    )
                    graph.record_event(event)
                    # Workers report back
                    event_back = InteractionEvent(
                        source_agent_id=worker.id,
                        target_agent_id=manager.id,
                        event_type=EventType.AGENT_RESPONSE,
                        payload={"process": "hierarchical", "inferred": True},
                    )
                    graph.record_event(event_back)
            else:
                # Parallel: all → all
                for i, src in enumerate(nodes):
                    for j, dst in enumerate(nodes):
                        if i != j:
                            event = InteractionEvent(
                                source_agent_id=src.id,
                                target_agent_id=dst.id,
                                event_type=EventType.TASK_DELEGATE,
                                payload={"process": "parallel", "inferred": True},
                            )
                            graph.record_event(event)

    def _build_node(self, raw_agent: Any) -> AgentNode:
        name = (
            getattr(raw_agent, "name", None)
            or getattr(raw_agent, "role", None)
            or type(raw_agent).__name__
        )
        role = getattr(raw_agent, "role", "unknown") or "unknown"
        goal = getattr(raw_agent, "goal", "") or ""
        backstory = getattr(raw_agent, "backstory", "") or ""
        tools = getattr(raw_agent, "tools", []) or []
        tool_names = []
        for t in tools:
            tool_names.append(
                getattr(t, "name", None) or getattr(t, "__name__", None) or type(t).__name__
            )

        return AgentNode(
            name=str(name),
            role=str(role),
            framework=self.framework_name,
            metadata={
                "goal": str(goal)[:200],
                "backstory": str(backstory)[:200],
                "tools": tool_names,
                "allow_delegation": getattr(raw_agent, "allow_delegation", False),
                "verbose": getattr(raw_agent, "verbose", False),
            },
        )
