"""
Sensitive Data Scanner Demo — proves SensitiveDataScanner detects 20+ patterns.

Builds a 3-agent mock workflow where agents deliberately pass sensitive data:
  DataCollector → Processor → Reporter

Each handoff contains realistic sensitive patterns (AWS keys, PII, DB strings,
JWT tokens, credit cards) that the context_leakage test should flag.

Run:
    python examples/sensitive_data_demo.py
    python examples/sensitive_data_demo.py --html
    python examples/sensitive_data_demo.py --json
"""

from __future__ import annotations

import argparse

from swarm_test import AgentNode, EventType, InteractionEvent, SwarmProbe


def main() -> None:
    parser = argparse.ArgumentParser(description="Sensitive data scanner demo")
    parser.add_argument("--html", action="store_true", help="Export HTML report")
    parser.add_argument("--json", action="store_true", help="Export JSON report")
    args = parser.parse_args()

    # -- Agents ---------------------------------------------------------------
    collector = AgentNode(name="DataCollector", role="researcher")
    processor = AgentNode(name="Processor", role="analyst")
    reporter = AgentNode(name="Reporter", role="writer")

    # -- Events with deliberately leaked sensitive data -----------------------

    # DataCollector → Processor: AWS key + email (PII)
    event1 = InteractionEvent(
        source_agent_id=collector.id,
        target_agent_id=processor.id,
        event_type=EventType.CONTEXT_SHARE,
        payload={
            "aws_credentials": "Access key: AKIAIOSFODNN7EXAMPLE",
            "contact": "Notify user@company.com when done",
        },
        duration_ms=120.0,
    )

    # Processor → Reporter: DB connection string + JWT token
    event2 = InteractionEvent(
        source_agent_id=processor.id,
        target_agent_id=reporter.id,
        event_type=EventType.TASK_DELEGATE,
        payload={
            "db_config": "postgresql://admin:password123@prod-db.internal:5432/users",
            "auth_token": (
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
                ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0"
                ".Sfl_KxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
        },
        duration_ms=350.0,
    )

    # Reporter output: credit card number in result
    event3 = InteractionEvent(
        source_agent_id=reporter.id,
        target_agent_id=collector.id,
        event_type=EventType.AGENT_RESPONSE,
        payload={
            "report": "Payment processed for card 4111-1111-1111-1111, confirmation sent.",
            "internal_note": "Debug endpoint at 192.168.1.50:8080/admin",
        },
        duration_ms=200.0,
    )

    # -- Probe ----------------------------------------------------------------
    print("Building 3-agent sensitive data demo...")
    probe = SwarmProbe(
        swarm_name="sensitive-data-demo",
        framework="static",
        agents=[collector, processor, reporter],
        events=[event1, event2, event3],
    )

    print(f"  agents: {probe.graph.graph.number_of_nodes()}")
    print(f"  edges:  {probe.graph.graph.number_of_edges()}")

    print("\nRunning all 6 reliability tests...")
    report = probe.run_all()
    report.print_summary()

    if args.html:
        from swarm_test.reporters.html import HtmlReporter

        path = HtmlReporter().render_with_graph(
            report, probe.graph, "sensitive_data_report.html"
        )
        print(f"\nHTML report saved: {path}")

    if args.json:
        report.to_json("sensitive_data_report.json", graph=probe.graph)
        print("JSON report saved: sensitive_data_report.json")


if __name__ == "__main__":
    main()
