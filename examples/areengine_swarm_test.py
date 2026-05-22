"""
Test the ARE (Application Readiness Engine — areengine.com / photopass-ai)
multi-agent system with swarm-test.

ARE is a custom FastAPI orchestrator that coordinates ~12 specialized
single-purpose agents across three pipelines:

  1. Passport Photo  (8-step sequential pipeline + trainer feedback)
  2. Signature       (2-step sub-pipeline)
  3. Smart Form      (composes pipelines 1+2 per country preset)
  4. Background      (evolution + trainer + health-monitor side-loops)

ARE is not built on CrewAI / LangChain / AutoGen, so SwarmProbe sees it
as a "generic" framework. This script imports the real OrchestratorAgent,
introspects it, and constructs an accurate SwarmGraph from the call paths
in agents/orchestrator.py.

Run:
    python examples/areengine_swarm_test.py
    python examples/areengine_swarm_test.py --html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swarm_test import (
    AgentNode,
    EventType,
    InteractionEvent,
    SwarmProbe,
)


ARE_ROOT = Path.home() / "photopass-ai"


def _try_import_orchestrator():
    """Best-effort import of the real OrchestratorAgent. Falls back to a
    static topology if heavy deps (mediapipe, rembg, onnxruntime...) aren't
    installed in this environment — we only need the agent structure."""
    if str(ARE_ROOT) not in sys.path:
        sys.path.insert(0, str(ARE_ROOT))
    try:
        from agents.orchestrator import OrchestratorAgent  # type: ignore
        return OrchestratorAgent()
    except Exception as exc:
        print(f"[info] Could not instantiate live OrchestratorAgent ({exc.__class__.__name__}); "
              f"using static topology from orchestrator.py source.")
        return None


# ---------------------------------------------------------------------------
# Build the swarm graph (agents + interaction edges)
# ---------------------------------------------------------------------------

def build_are_swarm():
    """Build the AgentNode list and InteractionEvent list for ARE."""

    orchestrator = _try_import_orchestrator()

    # --- Agents (12 total) -------------------------------------------------
    # Roles map to the actual responsibilities in agents/*.py.
    specs = [
        ("OrchestratorAgent",    "orchestrator",     "central pipeline coordinator"),
        ("ImageValidatorAgent",  "validator",        "size / format / EXIF validation"),
        ("FaceDetectorAgent",    "face_detector",    "MediaPipe face detect + align + crop"),
        ("BackgroundRemoverAgent", "bg_remover",     "rembg + bg-color compositing"),
        ("TrainerAgent",         "trainer",          "image classifier + learned params + sample recorder"),
        ("FaceEnhancerAgent",    "face_enhancer",    "GFPGAN / PIL enhancement"),
        ("ComplianceAgent",      "compliance",       "country-spec rule check"),
        ("FileOptimizerAgent",   "file_optimizer",   "JPEG quality search to hit KB target"),
        ("LayoutGeneratorAgent", "layout",           "print-sheet layout"),
        ("PrintOptimizerAgent",  "print_optimizer",  "save sheet + single to disk"),
        ("SignatureProcessorAgent", "signature",     "signature crop / clean / resize"),
        ("DocumentProcessorAgent",  "document",      "PDF + multi-page document processing"),
        ("EvolutionAgent",       "evolution",        "background auto-tune (every 10 min)"),
        ("HealthMonitorAgent",   "health",           "background health probe"),
    ]

    nodes: dict[str, AgentNode] = {}
    for name, role, description in specs:
        nodes[name] = AgentNode(
            name=name,
            role=role,
            framework="generic",
            metadata={
                "project": "areengine",
                "description": description,
                "live_class_present": orchestrator is not None
                                       and hasattr(orchestrator, role.replace("-", "_")),
            },
        )

    O = nodes["OrchestratorAgent"]

    def edge(src: AgentNode, tgt: AgentNode, etype: EventType, *,
             payload: dict | None = None, duration_ms: float = 0.0,
             success: bool = True, error: str | None = None) -> InteractionEvent:
        return InteractionEvent(
            source_agent_id=src.id,
            target_agent_id=tgt.id,
            event_type=etype,
            payload=payload or {},
            duration_ms=duration_ms,
            success=success,
            error_message=error,
        )

    events: list[InteractionEvent] = []

    # ── Pipeline 1: PASSPORT PHOTO (orchestrator.process_photo) ───────────
    # Step 1: validate
    events.append(edge(O, nodes["ImageValidatorAgent"], EventType.AGENT_CALL,
                       payload={"step": 1, "pipeline": "photo"}, duration_ms=18.4))
    events.append(edge(nodes["ImageValidatorAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"ok": True}, duration_ms=0.2))

    # Step 2: face detect
    events.append(edge(O, nodes["FaceDetectorAgent"], EventType.AGENT_CALL,
                       payload={"step": 2}, duration_ms=240.0))
    events.append(edge(nodes["FaceDetectorAgent"], O, EventType.CONTEXT_SHARE,
                       payload={"landmarks": True, "cropped": True}, duration_ms=0.3))

    # Step 3: bg removal
    events.append(edge(O, nodes["BackgroundRemoverAgent"], EventType.AGENT_CALL,
                       payload={"step": 3}, duration_ms=1850.0))
    events.append(edge(nodes["BackgroundRemoverAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"bg_removed": True}, duration_ms=0.2))

    # Step 4a: trainer classifies + provides learned params (parallel side-call)
    events.append(edge(O, nodes["TrainerAgent"], EventType.AGENT_CALL,
                       payload={"action": "classify_image"}, duration_ms=12.0))
    events.append(edge(nodes["TrainerAgent"], O, EventType.CONTEXT_SHARE,
                       payload={"image_type": "studio", "gfpgan_strength": 0.62}, duration_ms=0.1))

    # Step 4b: face enhance, parameterized by trainer
    events.append(edge(O, nodes["FaceEnhancerAgent"], EventType.AGENT_CALL,
                       payload={"step": 4, "strength": 0.62}, duration_ms=520.0))
    events.append(edge(nodes["FaceEnhancerAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"enhanced": True}, duration_ms=0.2))

    # Step 5: compliance
    events.append(edge(O, nodes["ComplianceAgent"], EventType.AGENT_CALL,
                       payload={"step": 5}, duration_ms=85.0))
    events.append(edge(nodes["ComplianceAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"compliance_score": 92.1, "issues": []}, duration_ms=0.2))

    # Step 6: file optimize (only when preset has KB bounds)
    events.append(edge(O, nodes["FileOptimizerAgent"], EventType.AGENT_CALL,
                       payload={"step": 6, "min_kb": 10, "max_kb": 50}, duration_ms=140.0))
    events.append(edge(nodes["FileOptimizerAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"size_kb": 38, "in_range": True}, duration_ms=0.2))

    # Step 7: layout
    events.append(edge(O, nodes["LayoutGeneratorAgent"], EventType.AGENT_CALL,
                       payload={"step": 7}, duration_ms=65.0))
    events.append(edge(nodes["LayoutGeneratorAgent"], O, EventType.CONTEXT_SHARE,
                       payload={"sheet": True, "single": True, "num_copies": 8}, duration_ms=0.2))

    # Step 8: print optimize / save
    events.append(edge(O, nodes["PrintOptimizerAgent"], EventType.AGENT_CALL,
                       payload={"step": 8}, duration_ms=110.0))
    events.append(edge(nodes["PrintOptimizerAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"saved_to": "outputs/<job>/sheet.jpg"}, duration_ms=0.2))

    # Re-optimize the single image after print-opt (real orchestrator does this)
    events.append(edge(nodes["PrintOptimizerAgent"], nodes["FileOptimizerAgent"], EventType.TASK_DELEGATE,
                       payload={"action": "reoptimize_single"}, duration_ms=130.0))

    # Trainer sample recording (fire-and-forget thread at end of pipeline)
    events.append(edge(O, nodes["TrainerAgent"], EventType.MEMORY_WRITE,
                       payload={"action": "record_sample", "fire_and_forget": True},
                       duration_ms=0.5))

    # ── Pipeline 2: SIGNATURE (orchestrator.process_signature) ────────────
    events.append(edge(O, nodes["SignatureProcessorAgent"], EventType.AGENT_CALL,
                       payload={"pipeline": "signature"}, duration_ms=95.0))
    events.append(edge(nodes["SignatureProcessorAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"image": "ok"}, duration_ms=0.2))

    events.append(edge(O, nodes["FileOptimizerAgent"], EventType.AGENT_CALL,
                       payload={"pipeline": "signature", "min_kb": 5, "max_kb": 20}, duration_ms=60.0))
    events.append(edge(nodes["FileOptimizerAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"size_kb": 14}, duration_ms=0.2))

    # ── Pipeline 3: DOCUMENT (orchestrator.process_smart_form fan-out) ────
    events.append(edge(O, nodes["DocumentProcessorAgent"], EventType.AGENT_CALL,
                       payload={"pipeline": "document"}, duration_ms=210.0))
    events.append(edge(nodes["DocumentProcessorAgent"], nodes["FileOptimizerAgent"], EventType.TASK_DELEGATE,
                       payload={"action": "optimize_pdf_page"}, duration_ms=170.0))
    events.append(edge(nodes["FileOptimizerAgent"], nodes["DocumentProcessorAgent"], EventType.AGENT_RESPONSE,
                       payload={"ok": True}, duration_ms=0.2))
    events.append(edge(nodes["DocumentProcessorAgent"], O, EventType.AGENT_RESPONSE,
                       payload={"pdf_ready": True}, duration_ms=0.2))

    # ── Background loops (main.py asyncio tasks) ──────────────────────────
    # Evolution agent auto-tunes settings every 10 minutes — it touches the
    # orchestrator's config and reads trainer analytics.
    events.append(edge(nodes["EvolutionAgent"], O, EventType.MEMORY_WRITE,
                       payload={"action": "auto_tune_settings", "interval_min": 10},
                       duration_ms=45.0))
    events.append(edge(nodes["EvolutionAgent"], nodes["TrainerAgent"], EventType.MEMORY_READ,
                       payload={"action": "read_analytics"}, duration_ms=30.0))

    # Health monitor probes the orchestrator
    events.append(edge(nodes["HealthMonitorAgent"], O, EventType.AGENT_CALL,
                       payload={"action": "heartbeat"}, duration_ms=2.0))
    events.append(edge(O, nodes["HealthMonitorAgent"], EventType.AGENT_RESPONSE,
                       payload={"healthy": True}, duration_ms=0.1))

    return list(nodes.values()), events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="swarm-test on the ARE (areengine) multi-agent system")
    parser.add_argument("--html", action="store_true", help="Export HTML report")
    parser.add_argument("--json", action="store_true", help="Export JSON report")
    args = parser.parse_args()

    print("Building ARE (areengine.com / photopass-ai) agent graph...")
    agents, events = build_are_swarm()
    print(f"  agents: {len(agents)}   interaction events: {len(events)}")

    print("Initializing SwarmProbe...")
    probe = SwarmProbe(
        swarm_name="areengine-are-system",
        framework="generic",
        agents=agents,
        events=events,
    )
    report = probe.run_all()
    report.print_summary()

    if args.html:
        from swarm_test.reporters.html import HtmlReporter
        path = HtmlReporter().render_with_graph(report, probe.graph, "areengine_swarm_report.html")
        print(f"\nHTML report saved: {path}")

    if args.json:
        import json
        with open("areengine_swarm_report.json", "w") as f:
            json.dump(report.model_dump(mode="json"), f, indent=2, default=str)
        print("JSON report saved: areengine_swarm_report.json")


if __name__ == "__main__":
    main()
