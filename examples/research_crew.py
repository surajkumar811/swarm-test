"""
Example: 4-agent mock research crew tested with swarm-test.

This example creates a realistic mock crew (no API keys required)
and runs the full swarm reliability test suite against it.

Run:
    python examples/research_crew.py
    python examples/research_crew.py --html
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from swarm_test import SwarmProbe, AgentNode, InteractionEvent, EventType


# ---------------------------------------------------------------------------
# Mock agent objects that mimic a CrewAI-style interface
# ---------------------------------------------------------------------------

class MockTool:
    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, query: str) -> str:
        return f"[{self.name}] result for: {query}"


class MockAgent:
    """Simulates a CrewAI Agent without requiring the library."""

    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        backstory: str,
        tools: Optional[List[MockTool]] = None,
        allow_delegation: bool = False,
    ) -> None:
        self.name = name
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.tools = tools or []
        self.allow_delegation = allow_delegation
        self.verbose = True

    def execute_task(self, task_description: str, context: str = "") -> str:
        return (
            f"[{self.name}] completed: {task_description[:50]}. "
            f"Context received: {len(context)} chars."
        )


class MockTask:
    """Simulates a CrewAI Task."""

    def __init__(
        self,
        description: str,
        agent: MockAgent,
        context: Optional[List["MockTask"]] = None,
    ) -> None:
        self.description = description
        self.agent = agent
        self.context = context or []
        self.output: Optional[str] = None


class MockCrew:
    """Simulates a CrewAI Crew with sequential process."""

    def __init__(
        self,
        agents: List[MockAgent],
        tasks: List[MockTask],
        process: str = "sequential",
    ) -> None:
        self.agents = agents
        self.tasks = tasks
        self.process = process
        self.verbose = True

    def kickoff(self) -> str:
        results = []
        context_output = ""
        for task in self.tasks:
            output = task.agent.execute_task(task.description, context_output)
            task.output = output
            context_output = output
            results.append(output)
        return "\n".join(results)


# ---------------------------------------------------------------------------
# Build the mock crew
# ---------------------------------------------------------------------------

def build_research_crew() -> MockCrew:
    # Tools
    web_search = MockTool("WebSearch")
    arxiv = MockTool("ArxivSearch")
    code_exec = MockTool("CodeExecutor")
    file_writer = MockTool("FileWriter")

    # Agents
    researcher = MockAgent(
        name="Lead Researcher",
        role="researcher",
        goal="Find cutting-edge research on multi-agent AI systems",
        backstory="Senior AI researcher with 10 years of NLP experience",
        tools=[web_search, arxiv],
        allow_delegation=False,
    )

    analyst = MockAgent(
        name="Data Analyst",
        role="analyst",
        goal="Analyze research findings and extract key metrics",
        backstory="Expert in quantitative analysis and data visualization",
        tools=[code_exec],
        allow_delegation=False,
    )

    writer = MockAgent(
        name="Technical Writer",
        role="writer",
        goal="Synthesize findings into a comprehensive report",
        backstory="Former IEEE editor with expertise in AI publications",
        tools=[file_writer],
        allow_delegation=False,
    )

    reviewer = MockAgent(
        name="Quality Reviewer",
        role="reviewer",
        goal="Verify accuracy and completeness of the final report",
        backstory="Critical reviewer ensuring research integrity",
        tools=[],
        allow_delegation=False,
    )

    # Tasks with context dependencies
    research_task = MockTask(
        description="Research the latest advances in multi-agent AI coordination",
        agent=researcher,
    )

    analysis_task = MockTask(
        description="Analyze the research data and produce statistical summaries",
        agent=analyst,
        context=[research_task],  # depends on research
    )

    writing_task = MockTask(
        description="Write a comprehensive technical report based on analysis",
        agent=writer,
        context=[research_task, analysis_task],  # depends on both
    )

    review_task = MockTask(
        description="Review the report for accuracy and completeness",
        agent=reviewer,
        context=[writing_task],  # depends on writing
    )

    return MockCrew(
        agents=[researcher, analyst, writer, reviewer],
        tasks=[research_task, analysis_task, writing_task, review_task],
        process="sequential",
    )


# ---------------------------------------------------------------------------
# Additional: inject some realistic interaction events manually
# to give the probe richer data to analyze
# ---------------------------------------------------------------------------

def inject_events(probe: SwarmProbe) -> None:
    """Add realistic inter-agent events to the graph for richer analysis."""
    agents = list(probe.graph.agents.values())
    if len(agents) < 2:
        return

    # Simulate researcher → analyst knowledge transfer
    if len(agents) >= 2:
        probe.record_event(InteractionEvent(
            source_agent_id=agents[0].id,
            target_agent_id=agents[1].id,
            event_type=EventType.CONTEXT_SHARE,
            payload={
                "content": "Research findings: 47 papers analyzed, top frameworks identified",
                "word_count": 2500,
            },
            duration_ms=342.1,
            success=True,
        ))

    # Simulate analyst → writer
    if len(agents) >= 3:
        probe.record_event(InteractionEvent(
            source_agent_id=agents[1].id,
            target_agent_id=agents[2].id,
            event_type=EventType.CONTEXT_SHARE,
            payload={
                "analysis_summary": "Mean performance improvement: 34%. Variance: 0.12",
                "charts_generated": 5,
            },
            duration_ms=891.4,
            success=True,
        ))

    # Simulate writer → reviewer
    if len(agents) >= 4:
        probe.record_event(InteractionEvent(
            source_agent_id=agents[2].id,
            target_agent_id=agents[3].id,
            event_type=EventType.AGENT_CALL,
            payload={
                "report_sections": 8,
                "word_count": 12400,
                "draft_version": "1.0",
            },
            duration_ms=125.0,
            success=True,
        ))

        # Reviewer sends back feedback
        probe.record_event(InteractionEvent(
            source_agent_id=agents[3].id,
            target_agent_id=agents[2].id,
            event_type=EventType.AGENT_RESPONSE,
            payload={
                "approved": False,
                "revision_notes": "Section 3 needs more citations",
            },
            duration_ms=67.3,
            success=True,
        ))

        # Writer revises and re-submits
        probe.record_event(InteractionEvent(
            source_agent_id=agents[2].id,
            target_agent_id=agents[3].id,
            event_type=EventType.AGENT_CALL,
            payload={
                "report_sections": 8,
                "word_count": 12750,
                "draft_version": "1.1",
            },
            duration_ms=98.5,
            success=True,
        ))

        # Final approval
        probe.record_event(InteractionEvent(
            source_agent_id=agents[3].id,
            target_agent_id=agents[2].id,
            event_type=EventType.AGENT_RESPONSE,
            payload={"approved": True},
            duration_ms=45.0,
            success=True,
        ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="swarm-test example: research crew")
    parser.add_argument("--html", action="store_true", help="Export HTML report")
    parser.add_argument("--json", action="store_true", help="Export JSON report")
    args = parser.parse_args()

    print("Building mock 4-agent research crew...")
    crew = build_research_crew()

    print("Initializing SwarmProbe...")
    # --- 3-line API ---
    probe = SwarmProbe(crew, swarm_name="research-crew-demo")
    inject_events(probe)  # add realistic event data
    report = probe.run_all()
    report.print_summary()

    if args.html:
        from swarm_test.reporters.html import HtmlReporter
        reporter = HtmlReporter()
        path = reporter.render_with_graph(report, probe.graph, "swarm_report.html")
        print(f"\nHTML report saved: {path}")

    if args.json:
        import json
        data = report.model_dump(mode="json")
        with open("swarm_report.json", "w") as f:
            json.dump(data, f, indent=2, default=str)
        print("JSON report saved: swarm_report.json")


if __name__ == "__main__":
    main()
