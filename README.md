# swarm-test

**The first reliability testing framework for multi-agent AI systems.**

swarm-test builds a NetworkX interaction graph of your agent swarm and runs 5 automated chaos tests to surface cascade failures, context leakage, intent drift, collusion, and blast radius risks — all from a 3-line API.

```python
from swarm_test import SwarmProbe

probe  = SwarmProbe(crew)
report = probe.run_all()
report.print_summary()
```

---

## Features

| Test | What it checks |
|---|---|
| **Cascade Failure** | Which agents, if they fail, bring down the most of the swarm |
| **Context Leakage** | Sensitive data (credentials, PII) crossing agent boundaries |
| **Intent Drift** | Agents acting outside their role; prompt injection; goal hijacking |
| **Collusion Detection** | Dense cliques, echo chambers, orchestrator-bypass cycles |
| **Blast Radius** | Single points of failure, critical path, redundancy score |

---

## Installation

```bash
pip install swarm-test
# or with framework extras:
pip install "swarm-test[crewai]"
pip install "swarm-test[langchain]"
```

From source:

```bash
git clone https://github.com/swarm-test/swarm-test
cd swarm-test
pip install -e ".[dev]"
```

---

## Quick Start

### With a CrewAI crew

```python
from crewai import Crew, Agent, Task
from swarm_test import SwarmProbe

researcher = Agent(role="researcher", goal="...", backstory="...")
writer     = Agent(role="writer",     goal="...", backstory="...")
crew = Crew(agents=[researcher, writer], tasks=[...])

probe  = SwarmProbe(crew, swarm_name="my-crew")
report = probe.run_all()
report.print_summary()
report.to_html("report.html")   # D3 graph visualization
```

### Static graph (no live swarm)

```python
from swarm_test import SwarmProbe, AgentNode, InteractionEvent, EventType

a = AgentNode(name="Fetcher", role="researcher")
b = AgentNode(name="Summarizer", role="writer")

probe = SwarmProbe(
    swarm_name="my-swarm",
    agents=[a, b],
    events=[InteractionEvent(
        source_agent_id=a.id,
        target_agent_id=b.id,
        event_type=EventType.TASK_DELEGATE,
    )],
)
report = probe.run_all()
report.print_summary()
```

### CLI

```bash
# Run against a Python script containing a `crew` variable
swarm-test probe my_crew.py --output report.html --fail-on-critical

# Static scan from the command line
swarm-test scan \
  --agents Researcher --agents Analyst --agents Writer \
  --edges "Researcher:Analyst" --edges "Analyst:Writer" \
  --output report.html
```

---

## Architecture

```
swarm_test/
├── core/
│   ├── models.py       # Pydantic models (AgentNode, Finding, SwarmReport, …)
│   ├── graph.py        # NetworkX SwarmGraph
│   ├── interceptor.py  # Monkey-patch agent methods, sensitive-data scanner
│   └── probe.py        # SwarmProbe — main entry point
├── attacks/
│   ├── cascade.py          # Cascade failure simulation
│   ├── context_leakage.py  # Sensitive-data boundary check
│   ├── intent_drift.py     # Role violations + goal hijacking
│   ├── collusion.py        # Clique/echo-chamber/cycle detection
│   └── blast_radius.py     # Topological SPOF + redundancy analysis
├── integrations/
│   ├── base.py             # BaseAdapter
│   └── crewai_adapter.py   # CrewAI Crew ingestion
├── reporters/
│   ├── console.py          # Rich terminal output
│   └── html.py             # D3 force-directed graph report
└── cli.py                  # Click CLI
```

---

## Report Output

### Terminal (Rich)

```
─────────────────── SWARM-TEST RELIABILITY REPORT ───────────────────

 Summary
 Swarm: research-crew-demo    Framework: crewai
 Agents: 4   Edges: 6
 Risk Score: 45/100
 Duration: 12ms

╭─────────────────── Test Results ─────────────────────╮
│ Test                  Status   Findings  Critical  High │
│ cascade_failure       FAILED       2         1       1  │
│ context_leakage       PASSED       0         0       0  │
│ intent_drift          PASSED       0         0       0  │
│ collusion_detection   PASSED       0         0       0  │
│ blast_radius          FAILED       1         1       0  │
╰───────────────────────────────────────────────────────╯
```

### HTML Report

Interactive D3.js force-directed graph showing agent nodes, interaction edges, and color-coded findings.

---

## Extending

### Custom attack

```python
from swarm_test.attacks.base import BaseAttack
from swarm_test.core.models import Finding, Severity, TestResult

class MyCustomAttack(BaseAttack):
    name = "my_custom_attack"

    def run(self, graph):
        findings = []
        # ... analyze graph.graph, graph.events ...
        return TestResult(test_name=self.name, findings=findings)
```

### Custom adapter

```python
from swarm_test.integrations.base import BaseAdapter

class MyFrameworkAdapter(BaseAdapter):
    framework_name = "my-framework"

    def _ingest_impl(self, swarm, graph):
        for raw_agent in swarm.my_agents:
            node = self._make_agent_node(raw_agent.name, raw_agent.role)
            graph.add_agent(node)
```

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v --cov=swarm_test
ruff check swarm_test/
black swarm_test/
```

---

## License

MIT — see [LICENSE](LICENSE).
