"""AutoGen framework adapter."""

from __future__ import annotations

import logging
from typing import Any

from swarm_test.core.graph import SwarmGraph
from swarm_test.core.models import AgentNode, EventType, InteractionEvent
from swarm_test.integrations.base import BaseAdapter

logger = logging.getLogger(__name__)


class AutoGenAdapter(BaseAdapter):
    """
    Adapter for AutoGen swarms.

    Supports:
    - ``GroupChat`` — extracts agents from ``groupchat.agents``
    - ``GroupChatManager`` — unwraps via ``manager.groupchat``
    - ``ConversableAgent`` / ``AssistantAgent`` / ``UserProxyAgent`` — lists or singletons

    Edges are inferred from:
    - ``allowed_or_disallowed_speaker_transitions`` / ``speaker_transitions_type``
    - ``allowed_transitions`` (mapping of agent → list[agent])
    - fully connected fallback when no transitions are configured
    """

    framework_name = "autogen"

    def _ingest_impl(self, swarm: Any, graph: SwarmGraph) -> None:
        groupchat = self._unwrap_groupchat(swarm)
        raw_agents = self._extract_raw_agents(swarm, groupchat)

        if not raw_agents:
            logger.warning("No agents found in AutoGen swarm object")
            return

        agent_map: dict[int, AgentNode] = {}
        nodes: list[AgentNode] = []
        for raw_agent in raw_agents:
            node = self._build_node(raw_agent)
            graph.add_agent(node)
            agent_map[id(raw_agent)] = node
            nodes.append(node)

        edges = self._infer_edges(swarm, groupchat, raw_agents, agent_map)

        for src_node, dst_node, edge_meta in edges:
            if src_node.id == dst_node.id:
                continue
            event = InteractionEvent(
                source_agent_id=src_node.id,
                target_agent_id=dst_node.id,
                event_type=EventType.TASK_DELEGATE,
                payload=edge_meta,
            )
            graph.record_event(event)

    # ------------------------------------------------------------------
    # Agent extraction (public API for tests / external callers)
    # ------------------------------------------------------------------

    def extract_agents(self, swarm_object: Any) -> list[AgentNode]:
        """Return ``AgentNode`` list for a GroupChat / Manager / agent list."""
        groupchat = self._unwrap_groupchat(swarm_object)
        raw_agents = self._extract_raw_agents(swarm_object, groupchat)
        return [self._build_node(a) for a in raw_agents]

    def extract_edges(self, swarm_object: Any) -> list[InteractionEvent]:
        """Return ``InteractionEvent`` list inferred from the swarm topology."""
        groupchat = self._unwrap_groupchat(swarm_object)
        raw_agents = self._extract_raw_agents(swarm_object, groupchat)
        if not raw_agents:
            return []
        agent_map: dict[int, AgentNode] = {}
        for raw_agent in raw_agents:
            agent_map[id(raw_agent)] = self._build_node(raw_agent)
        edges = self._infer_edges(swarm_object, groupchat, raw_agents, agent_map)
        events: list[InteractionEvent] = []
        for src_node, dst_node, edge_meta in edges:
            if src_node.id == dst_node.id:
                continue
            events.append(
                InteractionEvent(
                    source_agent_id=src_node.id,
                    target_agent_id=dst_node.id,
                    event_type=EventType.TASK_DELEGATE,
                    payload=edge_meta,
                )
            )
        return events

    # ------------------------------------------------------------------
    # Fault / context injection (for chaos tests)
    # ------------------------------------------------------------------

    def inject_failure(
        self,
        agent_id: str,
        failure_type: str,
        swarm_object: Any | None = None,
    ) -> None:
        """Monkey-patch ``generate_reply`` on the matching agent to raise an error."""
        if swarm_object is None:
            raise ValueError("inject_failure requires the live swarm_object")
        raw_agent = self._find_raw_agent(swarm_object, agent_id)
        if raw_agent is None:
            raise ValueError(f"Agent '{agent_id}' not found in swarm")

        message = f"Injected failure ({failure_type}) for agent '{agent_id}'"

        if failure_type in ("timeout", "hang"):

            def _patched(*args: Any, **kwargs: Any) -> Any:
                raise TimeoutError(message)

        elif failure_type in ("silent", "noop"):

            def _patched(*args: Any, **kwargs: Any) -> Any:
                return None

        else:

            def _patched(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError(message)

        raw_agent.generate_reply = _patched  # type: ignore[attr-defined]

    def inject_context(
        self,
        agent_id: str,
        context: dict[str, Any],
        swarm_object: Any | None = None,
    ) -> None:
        """Merge ``context`` into the agent's system_message / context dict."""
        if swarm_object is None:
            raise ValueError("inject_context requires the live swarm_object")
        raw_agent = self._find_raw_agent(swarm_object, agent_id)
        if raw_agent is None:
            raise ValueError(f"Agent '{agent_id}' not found in swarm")

        # Append to system_message if present
        existing_sys = getattr(raw_agent, "system_message", None)
        injected_str = "\n".join(f"{k}: {v}" for k, v in context.items())
        if isinstance(existing_sys, str):
            raw_agent.system_message = f"{existing_sys}\n[injected]\n{injected_str}"
        else:
            raw_agent.system_message = injected_str

        # Also merge into context dict if the attribute exists
        existing_ctx = getattr(raw_agent, "context", None)
        if isinstance(existing_ctx, dict):
            existing_ctx.update(context)
        else:
            try:
                raw_agent.context = dict(context)
            except Exception:
                pass

    def get_agent_output(
        self,
        agent_id: str,
        swarm_object: Any | None = None,
    ) -> Any:
        """Return the most recent message produced by the agent."""
        if swarm_object is None:
            raise ValueError("get_agent_output requires the live swarm_object")
        raw_agent = self._find_raw_agent(swarm_object, agent_id)
        if raw_agent is None:
            return None

        for attr in ("chat_messages", "_oai_messages", "messages"):
            buf = getattr(raw_agent, attr, None)
            if buf:
                if isinstance(buf, dict):
                    # Mapping peer-name → list[message]; return the latest across peers
                    latest: Any = None
                    for messages in buf.values():
                        if isinstance(messages, list) and messages:
                            latest = messages[-1]
                    if latest is not None:
                        return latest
                elif isinstance(buf, list) and buf:
                    return buf[-1]
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _unwrap_groupchat(swarm: Any) -> Any | None:
        """Return the underlying GroupChat, unwrapping a GroupChatManager if needed."""
        if swarm is None:
            return None
        # GroupChatManager exposes .groupchat
        groupchat = getattr(swarm, "groupchat", None)
        if groupchat is not None:
            return groupchat
        # Already a GroupChat
        if hasattr(swarm, "agents") and (
            "GroupChat" in type(swarm).__name__ or hasattr(swarm, "messages")
        ):
            return swarm
        return None

    @staticmethod
    def _extract_raw_agents(swarm: Any, groupchat: Any | None) -> list[Any]:
        """Extract the raw underlying agent objects."""
        # Prefer the GroupChat's agent list
        if groupchat is not None:
            agents = getattr(groupchat, "agents", None)
            if agents:
                return list(agents)

        # Plain list / tuple of ConversableAgent-like objects
        if isinstance(swarm, (list, tuple)):
            return [a for a in swarm if AutoGenAdapter._looks_like_agent(a)]

        # Single ConversableAgent-like object
        if AutoGenAdapter._looks_like_agent(swarm):
            return [swarm]

        # Fallback: attribute scan
        for attr in ("agents", "_agents", "members", "participants"):
            agents = getattr(swarm, attr, None)
            if agents:
                return list(agents)
        return []

    @staticmethod
    def _looks_like_agent(obj: Any) -> bool:
        if obj is None:
            return False
        cls_name = type(obj).__name__
        if any(
            marker in cls_name
            for marker in (
                "ConversableAgent",
                "AssistantAgent",
                "UserProxyAgent",
                "Agent",
            )
        ):
            return True
        # Duck-typing: has a name and either system_message or generate_reply
        return bool(
            getattr(obj, "name", None)
            and (
                hasattr(obj, "system_message")
                or hasattr(obj, "generate_reply")
                or hasattr(obj, "function_map")
            )
        )

    def _build_node(self, raw_agent: Any) -> AgentNode:
        name = getattr(raw_agent, "name", None) or type(raw_agent).__name__
        system_message = getattr(raw_agent, "system_message", "") or ""
        description = getattr(raw_agent, "description", "") or ""

        # Tools: function_map (dict[name → callable]) or registered_functions list
        tool_names: list[str] = []
        function_map = getattr(raw_agent, "function_map", None)
        if isinstance(function_map, dict):
            tool_names.extend(str(k) for k in function_map.keys())
        registered = getattr(raw_agent, "registered_functions", None) or getattr(
            raw_agent, "_function_map", None
        )
        if isinstance(registered, dict):
            for k in registered.keys():
                if str(k) not in tool_names:
                    tool_names.append(str(k))
        elif isinstance(registered, (list, tuple)):
            for fn in registered:
                fname = (
                    getattr(fn, "name", None)
                    or getattr(fn, "__name__", None)
                    or type(fn).__name__
                )
                if str(fname) not in tool_names:
                    tool_names.append(str(fname))

        return AgentNode(
            name=str(name),
            role=str(system_message)[:200] if system_message else "unknown",
            framework=self.framework_name,
            metadata={
                "goal": str(description)[:200],
                "system_message": str(system_message)[:500],
                "description": str(description)[:500],
                "tools": tool_names,
                "agent_type": type(raw_agent).__name__,
            },
        )

    def _infer_edges(
        self,
        swarm: Any,
        groupchat: Any | None,
        raw_agents: list[Any],
        agent_map: dict[int, AgentNode],
    ) -> list[tuple[AgentNode, AgentNode, dict[str, Any]]]:
        """Compute (src, dst, meta) triples for the swarm's interaction graph."""
        edges: list[tuple[AgentNode, AgentNode, dict[str, Any]]] = []

        # 1) Explicit allowed_or_disallowed_speaker_transitions on GroupChat
        transitions = None
        transitions_type = "allowed"
        sources_to_search = [swarm, groupchat]
        for src in sources_to_search:
            if src is None:
                continue
            transitions = getattr(src, "allowed_or_disallowed_speaker_transitions", None)
            if transitions is None:
                transitions = getattr(src, "allowed_transitions", None)
            if transitions is None:
                transitions = getattr(src, "speaker_transitions", None)
            t_type = getattr(src, "speaker_transitions_type", None)
            if t_type:
                transitions_type = str(t_type)
            if transitions:
                break

        if isinstance(transitions, dict) and transitions:
            allowed = str(transitions_type).lower().startswith("allow")
            if allowed:
                for src_agent, dsts in transitions.items():
                    src_node = self._resolve_agent_node(src_agent, raw_agents, agent_map)
                    if src_node is None:
                        continue
                    dst_list = dsts if isinstance(dsts, (list, tuple, set)) else [dsts]
                    for dst_agent in dst_list:
                        dst_node = self._resolve_agent_node(dst_agent, raw_agents, agent_map)
                        if dst_node is None:
                            continue
                        edges.append(
                            (
                                src_node,
                                dst_node,
                                {
                                    "edge_type": "speaker_transition",
                                    "transitions_type": transitions_type,
                                },
                            )
                        )
                return edges
            else:
                # Disallowed: build fully connected then subtract disallowed pairs
                disallowed_pairs: set[tuple[str, str]] = set()
                for src_agent, dsts in transitions.items():
                    src_node = self._resolve_agent_node(src_agent, raw_agents, agent_map)
                    if src_node is None:
                        continue
                    dst_list = dsts if isinstance(dsts, (list, tuple, set)) else [dsts]
                    for dst_agent in dst_list:
                        dst_node = self._resolve_agent_node(dst_agent, raw_agents, agent_map)
                        if dst_node is None:
                            continue
                        disallowed_pairs.add((src_node.id, dst_node.id))
                nodes = [agent_map[id(a)] for a in raw_agents if id(a) in agent_map]
                for src_n in nodes:
                    for dst_n in nodes:
                        if src_n.id == dst_n.id:
                            continue
                        if (src_n.id, dst_n.id) in disallowed_pairs:
                            continue
                        edges.append(
                            (
                                src_n,
                                dst_n,
                                {
                                    "edge_type": "speaker_transition",
                                    "transitions_type": transitions_type,
                                },
                            )
                        )
                return edges

        # 2) Speaker selection method customizes who speaks next — use it as a hint
        speaker_selection = None
        for src in sources_to_search:
            if src is None:
                continue
            speaker_selection = getattr(src, "speaker_selection_method", None)
            if speaker_selection:
                break

        # 3) Default: fully connected bidirectional graph
        nodes = [agent_map[id(a)] for a in raw_agents if id(a) in agent_map]
        meta: dict[str, Any] = {"edge_type": "groupchat_default"}
        if speaker_selection:
            meta["speaker_selection_method"] = str(speaker_selection)
        for src_n in nodes:
            for dst_n in nodes:
                if src_n.id == dst_n.id:
                    continue
                edges.append((src_n, dst_n, dict(meta)))
        return edges

    @staticmethod
    def _resolve_agent_node(
        ref: Any,
        raw_agents: list[Any],
        agent_map: dict[int, AgentNode],
    ) -> AgentNode | None:
        """Resolve a name string or agent instance to its AgentNode."""
        if ref is None:
            return None
        if id(ref) in agent_map:
            return agent_map[id(ref)]
        if isinstance(ref, str):
            for raw in raw_agents:
                if getattr(raw, "name", None) == ref:
                    return agent_map.get(id(raw))
        return None

    def _find_raw_agent(self, swarm: Any, agent_id: str) -> Any | None:
        """Find a raw agent by AgentNode id or by name."""
        groupchat = self._unwrap_groupchat(swarm)
        raw_agents = self._extract_raw_agents(swarm, groupchat)
        # Build temporary map to resolve id
        for raw in raw_agents:
            node = self._build_node(raw)
            if node.id == agent_id or raw.name == agent_id:
                return raw
            # Also match by name comparison since AgentNode ids are fresh uuids
            if getattr(raw, "name", None) == agent_id:
                return raw
        return None
