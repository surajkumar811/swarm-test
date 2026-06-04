# Black_Wall integration (pre-action risk gate)

swarm-test scores each agent's structural health `0–100`. [Black_Wall](https://blackwalltier.com) — a pre-action risk gate for AI agents — consumes that score as a **downside-only prior**: a structurally fragile agent gets extra caution on irreversible actions, but a healthy score never weakens the gate.

## Wiring it in

Attach the score to any `forecast()` call under `context.agent_health`:

```jsonc
POST https://blackwalltier.com/api/v1/forecast
Authorization: Bearer <your_key>
{
  "action": "publish",
  "inputs": { "channel": "public_status_page", "content": "..." },
  "context": {
    "agent_role": "ml pipeline agent",
    "agent_health": { "score": 4 }      // from swarm-test · 0–100, higher = healthier
  }
}
```

`agent_health` is optional and additive — omit it and the forecast is unchanged.

## How the score moves the verdict

A bounded, reversibility-gated penalty:

```
pressure = score >= 64 ? 0 : (64 - score) / 64
penalty  = round(28 * pressure * blast)        # blast: irreversible 1 · recoverable 0.5 · reversible 0
adjusted_risk = min(100, base_risk + penalty)
```

| swarm-test score | effect on an irreversible action |
|---|---|
| 0–10 (e.g. a SPOF agent at 4) | strong pressure (up to +26) — tips borderline → **STOP** |
| 11–63 | graduated pressure ∝ `(64 − score)` |
| 64–100 | **neutral** — no thumb on the scale |

**Guarantees:**

- **Downside-only** — can only add caution; a healthy score never clears a STOP the action itself warrants.
- **Reversibility-gated** — a fragile agent doing a read-only / reversible action gets ≈0 penalty.
- **Fail-open** — a malformed or out-of-range score is ignored (a bad hint never itself causes a STOP).
- The applied penalty surfaces as an `AGENT_HEALTH_PENALTY` red flag, and the score is committed in the response's **Ed25519-signed receipt** — so each verdict is cryptographically bound to the exact score it saw.

## Worked example

The six agents from a swarm-test `agent_health` export, run through the live gate on one fixed irreversible action (`publish` to a public status page — identical inputs, varying **only** `agent_health.score`):

| agent | score | health penalty | adjusted risk | verdict |
|---|---|---|---|---|
| OrchestratorAgent | 4 | +26 | 84 | **STOP** |
| FaceDetectorAgent | 44 | +9 | 67 | CAUTION |
| EvolutionAgent | 50 | +6 | 64 | CAUTION |
| FileOptimizerAgent | 54 | +4 | 66 | CAUTION |
| TrainerAgent | 56 | +4 | 62 | CAUTION |
| ImageValidatorAgent | 64 | — (neutral) | 62 | CAUTION |
| *(no agent_health)* | — | — | 58 | CAUTION |

The fragile OrchestratorAgent (score 4) tips a borderline action **CAUTION → STOP**; score 64 is correctly neutral. On a read-only `query`, the same fragile agent gets **zero** penalty (reversibility-gated) and stays **GO**. Every row is an Ed25519-signed receipt.

## Verifying

Verify any verdict at `https://blackwalltier.com/api/v1/receipts/verify`, or fetch the public key from `https://blackwalltier.com/.well-known/blackwall-signing-keys.json` and verify offline.

## Get a key

Free tier is ~100 forecasts/month — https://blackwalltier.com/dashboard/keys
