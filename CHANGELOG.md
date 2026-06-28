# Changelog

All notable changes to swarm-test are documented in this file. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Scoring is role-aware.** Intentional orchestrator hubs (declared via
  `intentional_role="ORCHESTRATOR"`) are no longer flagged as critical
  single-points-of-failure or cascade sources — their centrality is by design.
  The cascade attack now uses `get_effective_blast_radius` for spokes so a
  worker that only returns to the hub stops inheriting the hub's reach.
- **Agent health scores are role-aware.** `AgentHealthScorer` applies
  `role_adjusted_severity` consistently across centrality penalties (blast
  radius, SPOF, cascade depth, edge imbalance). Declared hubs get full
  centrality suppression; inferred hubs get one-notch downgrade; non-hub
  workers are unchanged. A clean intentional hub now lands in the healthy
  band (≥70) instead of scoring ~4/100 for expected centrality.
- **Findings are deduplicated on cycles.** A cycle (e.g. `A→B→C→A`) used to
  emit N separate cascade findings (one per member) and N per-edge
  "fragile dependency" findings in both `cost_risk` and
  `timeout_resilience`. They now collapse into one cascade record and one
  cycle-fragile record per attack; the unbounded-loop CRITICAL stays intact.
- **CLI quick-scan moved to `swarm-test scan`.** The dedicated subcommand for
  inline `--agents`/`--edges` topology testing is now `swarm-test scan`. The
  README quickstart and `run` help text reflect this. (`run` still accepts
  `-a`/`-e` for back-compat but the canonical inline form is `scan`.)

### Fixed
- **Small-graph role misclassification.** Spokes in a 4-node hub-and-spoke
  (e.g. `Hub:orchestrator,A,B,C`) were being classified as ORCHESTRATOR at
  42% confidence; members of a 3-node cycle (`A→B→C→A`) at 65%. The
  `classify_agent` heuristic now requires absolute structural thresholds
  (out-degree ≥3 with high betweenness, or pure-source fan-out with
  in-degree=0) instead of ratio-only scoring that tripped on tiny graphs.
  Same fix applied to AGGREGATOR (in-degree ≥3) and VALIDATOR (in-degree ≥2)
  so cycle members no longer get tagged as security-sensitive validators.
- **Safety check:** a worker mis-classified as an inferred hub on a small
  graph would have unlocked centrality suppression for its findings. The
  stricter thresholds plus a regression test
  (`test_misclassified_hub_does_not_suppress_real_findings`) prevent any
  non-hub from receiving role-aware suppression.

### Added
- `tests/test_role_aware_findings.py` — 15 acceptance tests covering the
  role-aware finding pipeline (clean hub topology, unbounded bypass cycles,
  orchestrator-bypass cycles, severity-adjustment wiring, hub vs spoke
  classification, cycle dedup) and the safety-check regression guards.
- `swarm_test/core/graph.py::get_effective_blast_radius` — hub-excluding
  reachability that powers the role-aware cascade and health calculations.
