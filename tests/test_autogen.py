"""Tests for the AutoGen adapter."""

from __future__ import annotations

import pytest

from swarm_test import SwarmProbe
from swarm_test.core.graph import SwarmGraph
from swarm_test.integrations.autogen_adapter import AutoGenAdapter

# ---------------------------------------------------------------------------
# Mock AutoGen objects (no pyautogen dependency needed)
# ---------------------------------------------------------------------------


class MockConversableAgent:
    """Mimics autogen.ConversableAgent."""

    def __init__(
        self,
        name: str,
        system_message: str = "",
        description: str = "",
        function_map: dict | None = None,
    ) -> None:
        self.name = name
        self.system_message = system_message
        self.description = description
        self.function_map = function_map or {}
        self.chat_messages: dict[str, list[dict]] = {}

    def generate_reply(self, *args, **kwargs):
        return f"{self.name}: response"


class MockGroupChat:
    """Mimics autogen.GroupChat."""

    def __init__(
        self,
        agents: list,
        messages: list | None = None,
        allowed_or_disallowed_speaker_transitions: dict | None = None,
        speaker_transitions_type: str = "allowed",
        speaker_selection_method: str = "auto",
    ) -> None:
        self.agents = agents
        self.messages = messages or []
        self.allowed_or_disallowed_speaker_transitions = allowed_or_disallowed_speaker_transitions
        self.speaker_transitions_type = speaker_transitions_type
        self.speaker_selection_method = speaker_selection_method


class MockGroupChatManager:
    """Mimics autogen.GroupChatManager."""

    def __init__(self, groupchat: MockGroupChat, name: str = "manager") -> None:
        self.groupchat = groupchat
        self.name = name


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_4_agent_groupchat(transitions: dict | None = None) -> MockGroupChat:
    planner = MockConversableAgent(
        name="Planner",
        system_message="You break work into tasks and assign them.",
        description="Orchestrator",
        function_map={"create_task": lambda x: x, "assign": lambda x: x},
    )
    coder = MockConversableAgent(
        name="Coder",
        system_message="You implement coding tasks.",
        function_map={"write_code": lambda x: x},
    )
    reviewer = MockConversableAgent(
        name="Reviewer",
        system_message="You review the coder's output.",
    )
    executor = MockConversableAgent(
        name="Executor",
        system_message="You run approved code.",
    )
    return MockGroupChat(
        agents=[planner, coder, reviewer, executor],
        allowed_or_disallowed_speaker_transitions=transitions,
        speaker_transitions_type="allowed" if transitions else "allowed",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAutoGenAdapter:
    def test_autogen_adapter_extract_agents_from_groupchat(self):
        """extract_agents pulls 4 agents out of a GroupChat."""
        gc = _build_4_agent_groupchat()
        adapter = AutoGenAdapter()

        agents = adapter.extract_agents(gc)
        assert len(agents) == 4
        names = [a.name for a in agents]
        assert names == ["Planner", "Coder", "Reviewer", "Executor"]
        # Manager unwrapping
        manager = MockGroupChatManager(gc)
        agents_via_manager = adapter.extract_agents(manager)
        assert len(agents_via_manager) == 4

    def test_autogen_adapter_extract_edges_fully_connected(self):
        """Without transitions, agents are fully connected (bidirectional)."""
        gc = _build_4_agent_groupchat()
        gc.allowed_or_disallowed_speaker_transitions = None

        graph = SwarmGraph()
        adapter = AutoGenAdapter()
        adapter.ingest(gc, graph)

        n = graph.graph.number_of_nodes()
        e = graph.graph.number_of_edges()
        assert n == 4
        # Fully-connected directed graph (excluding self-loops): n * (n - 1)
        assert e == n * (n - 1)
        # Bidirectional: for every (a, b) edge, (b, a) also exists
        edge_pairs = {(u, v) for u, v, _ in graph.graph.edges(keys=True)}
        for u, v in list(edge_pairs):
            assert (v, u) in edge_pairs

    def test_autogen_adapter_extract_edges_with_transitions(self):
        """allowed_or_disallowed_speaker_transitions defines directed edges."""
        agents = [
            MockConversableAgent(name="A", system_message="root"),
            MockConversableAgent(name="B", system_message="worker"),
            MockConversableAgent(name="C", system_message="reviewer"),
        ]
        transitions = {agents[0]: [agents[1]], agents[1]: [agents[2]]}
        gc = MockGroupChat(
            agents=agents,
            allowed_or_disallowed_speaker_transitions=transitions,
            speaker_transitions_type="allowed",
        )

        graph = SwarmGraph()
        adapter = AutoGenAdapter()
        adapter.ingest(gc, graph)

        assert graph.graph.number_of_nodes() == 3
        # Only the two configured edges should exist (not fully connected)
        assert graph.graph.number_of_edges() == 2
        names_by_id = {n: d["name"] for n, d in graph.graph.nodes(data=True)}
        pairs = {(names_by_id[u], names_by_id[v]) for u, v, _ in graph.graph.edges(keys=True)}
        assert pairs == {("A", "B"), ("B", "C")}

    def test_autogen_adapter_extract_tools(self):
        """function_map names are captured in agent metadata as tools."""
        gc = _build_4_agent_groupchat()
        adapter = AutoGenAdapter()
        agents = adapter.extract_agents(gc)

        planner = next(a for a in agents if a.name == "Planner")
        assert "create_task" in planner.metadata["tools"]
        assert "assign" in planner.metadata["tools"]

        coder = next(a for a in agents if a.name == "Coder")
        assert coder.metadata["tools"] == ["write_code"]

    def test_autogen_adapter_inject_failure(self):
        """inject_failure swaps generate_reply with one that raises."""
        gc = _build_4_agent_groupchat()
        adapter = AutoGenAdapter()
        adapter.inject_failure("Planner", "error", swarm_object=gc)

        planner = gc.agents[0]
        with pytest.raises(RuntimeError):
            planner.generate_reply()

    def test_autogen_adapter_detect_framework(self):
        """SwarmProbe._detect_framework returns 'autogen' for a GroupChat."""
        gc = _build_4_agent_groupchat()
        probe = SwarmProbe(gc, swarm_name="auto-detect")
        assert probe.framework == "autogen"

        manager = MockGroupChatManager(gc)
        probe_m = SwarmProbe(manager, swarm_name="auto-detect-mgr")
        assert probe_m.framework == "autogen"

    def test_autogen_adapter_agent_roles(self):
        """system_message is captured as the agent role."""
        gc = _build_4_agent_groupchat()
        adapter = AutoGenAdapter()
        agents = adapter.extract_agents(gc)

        planner = next(a for a in agents if a.name == "Planner")
        assert "break work into tasks" in planner.role
        assert planner.metadata["system_message"] == "You break work into tasks and assign them."

    def test_autogen_full_integration(self):
        """SwarmProbe runs all chaos tests end-to-end on an AutoGen GroupChat."""
        transitions = None  # use default fully connected
        gc = _build_4_agent_groupchat(transitions=transitions)

        probe = SwarmProbe(gc, swarm_name="autogen-integration")
        report = probe.run_all()

        assert report.framework == "autogen"
        assert report.agent_count == 4
        # All built-in chaos tests should execute.
        assert len(report.test_results) == 8
        test_names = {r.test_name for r in report.test_results}
        assert "timeout_resilience" in test_names
        assert "blast_radius" in test_names
        assert "trajectory_analysis" in test_names
        assert "cost_risk" in test_names
