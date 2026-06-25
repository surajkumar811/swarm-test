"""Tests for the LangGraph adapter."""

from __future__ import annotations

from swarm_test import SwarmProbe
from swarm_test.core.graph import SwarmGraph
from swarm_test.integrations.langgraph_adapter import LangGraphAdapter

# ---------------------------------------------------------------------------
# Mock LangGraph objects (no langgraph dependency needed)
# ---------------------------------------------------------------------------


class MockStateGraph:
    """Mimics langgraph.graph.StateGraph with nodes and edges."""

    def __init__(self):
        self.nodes: dict = {}
        self.edges: set[tuple[str, str]] = set()
        self.branches: dict = {}

    def add_node(self, name: str, fn=None):
        self.nodes[name] = fn or (lambda x: x)

    def add_edge(self, src: str, dst: str):
        self.edges.add((src, dst))

    def add_conditional_edges(self, src: str, targets: dict[str, str]):
        self.branches[src] = {"condition": targets}


class MockCompiledGraph:
    """Mimics a compiled LangGraph with a .builder reference."""

    def __init__(self, builder: MockStateGraph):
        self.builder = builder


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLangGraphAdapter:
    def test_ingest_simple_linear(self):
        """3-node linear graph: A → B → C."""
        sg = MockStateGraph()
        sg.add_node("researcher")
        sg.add_node("analyst")
        sg.add_node("writer")
        sg.add_edge("__start__", "researcher")
        sg.add_edge("researcher", "analyst")
        sg.add_edge("analyst", "writer")
        sg.add_edge("writer", "__end__")

        graph = SwarmGraph()
        adapter = LangGraphAdapter()
        adapter.ingest(sg, graph)

        assert graph.graph.number_of_nodes() == 3
        assert graph.graph.number_of_edges() == 2
        # Check entry/exit metadata
        agents = {a.name: a for a in graph.agents.values()}
        assert agents["researcher"].metadata.get("is_entry_point") is True
        assert agents["writer"].metadata.get("is_exit_point") is True

    def test_ingest_compiled_graph(self):
        """CompiledGraph wrapping a StateGraph should work via .builder."""
        sg = MockStateGraph()
        sg.add_node("planner")
        sg.add_node("executor")
        sg.add_edge("__start__", "planner")
        sg.add_edge("planner", "executor")
        sg.add_edge("executor", "__end__")

        compiled = MockCompiledGraph(sg)
        graph = SwarmGraph()
        adapter = LangGraphAdapter()
        adapter.ingest(compiled, graph)

        assert graph.graph.number_of_nodes() == 2
        assert graph.graph.number_of_edges() == 1

    def test_conditional_edges(self):
        """Conditional edges should create interactions to all branch targets."""
        sg = MockStateGraph()
        sg.add_node("router")
        sg.add_node("path_a")
        sg.add_node("path_b")
        sg.add_edge("__start__", "router")
        sg.add_conditional_edges("router", {"option_a": "path_a", "option_b": "path_b"})

        graph = SwarmGraph()
        adapter = LangGraphAdapter()
        adapter.ingest(sg, graph)

        assert graph.graph.number_of_nodes() == 3
        # Should have edges: router→path_a, router→path_b
        assert graph.graph.number_of_edges() == 2
        # Verify conditional metadata
        for event in graph.events:
            assert event.payload.get("edge_type") == "conditional"

    def test_role_inference(self):
        """Node names should map to meaningful roles."""
        sg = MockStateGraph()
        sg.add_node("research_agent")
        sg.add_node("summarizer")
        sg.add_node("validator")
        sg.add_edge("research_agent", "summarizer")
        sg.add_edge("summarizer", "validator")

        graph = SwarmGraph()
        adapter = LangGraphAdapter()
        adapter.ingest(sg, graph)

        agents = {a.name: a for a in graph.agents.values()}
        assert agents["research_agent"].role == "researcher"
        assert agents["summarizer"].role == "summarizer"
        assert agents["validator"].role == "validator"

    def test_probe_integration(self):
        """SwarmProbe should run all 6 tests on a LangGraph-ingested graph."""
        sg = MockStateGraph()
        sg.add_node("fetcher")
        sg.add_node("processor")
        sg.add_node("output")
        sg.add_edge("__start__", "fetcher")
        sg.add_edge("fetcher", "processor")
        sg.add_edge("processor", "output")
        sg.add_edge("output", "__end__")

        # Use the adapter directly since we don't have real langgraph
        graph = SwarmGraph()
        adapter = LangGraphAdapter()
        adapter.ingest(sg, graph)

        probe = SwarmProbe(
            swarm_name="lg-test",
            agents=list(graph.agents.values()),
            events=list(graph.events),
        )
        report = probe.run_all()

        assert len(report.test_results) == 8
        test_names = {r.test_name for r in report.test_results}
        assert "timeout_resilience" in test_names
        assert "trajectory_analysis" in test_names
        assert "cost_risk" in test_names
        assert report.agent_count == 3
