"""
AutoGen + swarm-test example.

Demonstrates how to test an AutoGen ``GroupChat`` of ``ConversableAgent``s
with SwarmProbe. Uses lightweight mock classes so this example runs without
``pyautogen`` installed.

Run:
    python examples/autogen_crew.py
    python examples/autogen_crew.py --html
"""

from __future__ import annotations

import argparse

from swarm_test import SwarmProbe


class MockConversableAgent:
    """Mimics ``autogen.ConversableAgent`` for ingestion testing."""

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
    """Mimics ``autogen.GroupChat`` — exposes ``.agents`` and transitions."""

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
        self.allowed_or_disallowed_speaker_transitions = (
            allowed_or_disallowed_speaker_transitions
        )
        self.speaker_transitions_type = speaker_transitions_type
        self.speaker_selection_method = speaker_selection_method


class MockGroupChatManager:
    """Mimics ``autogen.GroupChatManager`` — wraps a GroupChat."""

    def __init__(self, groupchat: MockGroupChat, name: str = "manager") -> None:
        self.groupchat = groupchat
        self.name = name


def build_autogen_swarm() -> MockGroupChat:
    """Build a 4-agent AutoGen-style workflow.

    Agents:
        Planner   — orchestrator
        Coder     — worker
        Reviewer  — validator
        Executor  — worker

    Transitions (allowed):
        Planner   → Coder, Executor
        Coder     → Reviewer
        Reviewer  → Planner       (review loop)
    """
    planner = MockConversableAgent(
        name="Planner",
        system_message="You break work into tasks and assign them.",
        description="Orchestrator agent",
        function_map={"create_task": lambda x: x, "assign": lambda x: x},
    )
    coder = MockConversableAgent(
        name="Coder",
        system_message="You implement the assigned coding tasks.",
        description="Implementation worker",
        function_map={"write_code": lambda x: x},
    )
    reviewer = MockConversableAgent(
        name="Reviewer",
        system_message="You review the coder's output and either approve or reject.",
        description="Validation agent",
        function_map={"review_code": lambda x: x, "approve": lambda x: x},
    )
    executor = MockConversableAgent(
        name="Executor",
        system_message="You run the approved code.",
        description="Execution worker",
        function_map={"run_code": lambda x: x},
    )

    transitions = {
        planner: [coder, executor],
        coder: [reviewer],
        reviewer: [planner],
    }

    return MockGroupChat(
        agents=[planner, coder, reviewer, executor],
        allowed_or_disallowed_speaker_transitions=transitions,
        speaker_transitions_type="allowed",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="swarm-test on an AutoGen-style GroupChat"
    )
    parser.add_argument("--html", action="store_true", help="Export HTML report")
    args = parser.parse_args()

    print("Building 4-agent AutoGen GroupChat...")
    groupchat = build_autogen_swarm()
    print(f"  agents: {len(groupchat.agents)}")

    print("Initializing SwarmProbe...")
    probe = SwarmProbe(groupchat, swarm_name="autogen-demo")
    report = probe.run_all()
    report.print_summary()

    if args.html:
        from swarm_test.reporters.html import HtmlReporter

        path = HtmlReporter().render_with_graph(
            report, probe.graph, "autogen_swarm_report.html"
        )
        print(f"\nHTML report saved: {path}")


# Export a default ``groupchat`` so ``swarm-test probe`` / ``run`` can pick it up.
groupchat = build_autogen_swarm()


if __name__ == "__main__":
    main()
