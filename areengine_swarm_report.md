# 🧪 Swarm Reliability Report — areengine-are-system

| Metric | Value |
|--------|-------|
| **Swarm** | areengine-are-system |
| **Framework** | generic |
| **Agents** | 14 |
| **Edges** | 32 |
| **Risk Score** | 🔴 100/100 |
| **Duration** | 6ms |

## Test Results

| Test | Status | Findings | Critical | High | Duration |
|------|--------|----------|----------|------|----------|
| cascade_failure | ❌ FAILED | 14 | **14** | 0 | 0.7ms |
| context_leakage | ✅ PASSED | 0 | 0 | 0 | 0.2ms |
| intent_drift | ❌ FAILED | 13 | 0 | 0 | 0.7ms |
| collusion_detection | ❌ FAILED | 4 | 0 | **4** | 1.7ms |
| blast_radius | ❌ FAILED | 1 | **1** | 0 | 2.2ms |
| timeout_resilience | ❌ FAILED | 22 | 0 | **9** | 0.3ms |

## Agent Health Scores

| Agent | Score | Status | Details |
|-------|-------|--------|---------|
| OrchestratorAgent | 🔴 4/100 | ❌ | 92% blast radius, SPOF, high cascade depth, 3 collusion clique(s), has fallback upstreams |
| FaceDetectorAgent | 🟡 44/100 | ⚠️ | 92% blast radius, high cascade depth |
| EvolutionAgent | 🟡 50/100 | ⚠️ | 100% blast radius, 1 collusion clique(s) |
| FileOptimizerAgent | 🟡 54/100 | ⚠️ | 92% blast radius, 2 collusion clique(s), has fallback upstreams |
| PrintOptimizerAgent | 🟡 54/100 | ⚠️ | 92% blast radius, 1 collusion clique(s) |
| TrainerAgent | 🟡 56/100 | ⚠️ | 92% blast radius, 1 collusion clique(s), has fallback upstreams, unbalanced edges |
| ImageValidatorAgent | 🟡 64/100 | ⚠️ | 92% blast radius |
| BackgroundRemoverAgent | 🟡 64/100 | ⚠️ | 92% blast radius |
| FaceEnhancerAgent | 🟡 64/100 | ⚠️ | 92% blast radius |
| ComplianceAgent | 🟡 64/100 | ⚠️ | 92% blast radius |
| LayoutGeneratorAgent | 🟡 64/100 | ⚠️ | 92% blast radius |
| SignatureProcessorAgent | 🟡 64/100 | ⚠️ | 92% blast radius |
| DocumentProcessorAgent | 🟡 64/100 | ⚠️ | 92% blast radius, 1 collusion clique(s), has fallback upstreams |
| HealthMonitorAgent | 🟡 64/100 | ⚠️ | 92% blast radius |

## Top Findings (54 total)

### 1. 🔴 **CRITICAL** — Catastrophic cascade potential: OrchestratorAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'OrchestratorAgent' (id=1789ac3b-a50c-487d-b332-bf7bd0685d7e) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 2. 🔴 **CRITICAL** — Catastrophic cascade potential: ImageValidatorAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'ImageValidatorAgent' (id=93473bb6-7971-4282-a0e8-b090db8b4dea) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 3. 🔴 **CRITICAL** — Catastrophic cascade potential: FaceDetectorAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'FaceDetectorAgent' (id=a23a6d32-183f-4b97-bcf2-c9661ba67fce) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 4. 🔴 **CRITICAL** — Catastrophic cascade potential: BackgroundRemoverAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'BackgroundRemoverAgent' (id=1083122d-bf2b-4aab-a346-a92d916c03d7) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 5. 🔴 **CRITICAL** — Catastrophic cascade potential: TrainerAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'TrainerAgent' (id=e3df9b73-03ef-4f67-bd88-d2978a354c8f) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 6. 🔴 **CRITICAL** — Catastrophic cascade potential: FaceEnhancerAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'FaceEnhancerAgent' (id=308d40e9-a48a-4436-a844-6b7c14bb2e57) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 7. 🔴 **CRITICAL** — Catastrophic cascade potential: ComplianceAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'ComplianceAgent' (id=c9b07318-fa9f-4570-82ce-d472498f3ada) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 8. 🔴 **CRITICAL** — Catastrophic cascade potential: FileOptimizerAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'FileOptimizerAgent' (id=4bafc444-f819-462c-9244-9f039e7a04ec) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 9. 🔴 **CRITICAL** — Catastrophic cascade potential: LayoutGeneratorAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'LayoutGeneratorAgent' (id=d48c9062-2111-4586-86c8-8e1990eee778) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

### 10. 🔴 **CRITICAL** — Catastrophic cascade potential: PrintOptimizerAgent failure cascades to 12 agents

**Test:** cascade_failure

Agent 'PrintOptimizerAgent' (id=d8aeb8fe-ec7a-480f-b113-3126b2bc777e) has a blast radius of 92.3% — failure would directly or indirectly impact 12 of 14 agents.

> **Remediation:** Introduce circuit breakers, health checks, and fallback agents to isolate failures. Consider replicating this agent.

*... and 44 more findings (see full JSON/HTML report)*

## Graph Metrics

| Metric | Value |
|--------|-------|
| Nodes | 14 |
| Edges | 32 |
| Density | 0.1758 |
| Cycles | 17 |
| SPOFs | 1 |
| Critical Path | 3 hops |
| Weakly Connected | Yes |

---

*Generated by [swarm-test](https://github.com/surajkumar811/swarm-test) v0.2.1 at 2026-06-01 17:21:35 UTC*
