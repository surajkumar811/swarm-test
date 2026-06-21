"""Historical tracking — persist swarm-test runs to disk and compute trends.

A ``HistoryStore`` writes a JSON snapshot of each run into a local directory
(default ``.swarmtest-history``) and exposes helpers to load recent entries,
compare the current run to the previous one, and prune old history.

Snapshots are deliberately compact (no enriched finding payloads) so that
several hundred entries cost only a few hundred kilobytes of disk space.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swarm_test.core.models import Severity, SwarmReport

# Severity ordering used to detect regressions (lower index = worse).
_SEVERITY_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low", "info")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_HISTORY_FILE_RE = re.compile(
    r"^(?P<ts>\d{8}-\d{6})_(?P<name>.+)\.json$",
)
_STABLE_TREND_BAND = 2  # |delta| <= this is "stable"
# Strip out embedded numeric values (counts, percentages, durations) from finding
# titles before hashing for identity. Counts that drift with small topology
# changes (e.g. "cascades to 12 agents" → "cascades to 13 agents") would
# otherwise treat the same finding as new/resolved.
_TITLE_NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?%?")


def _normalize_title(title: str) -> str:
    return _TITLE_NUMERIC_RE.sub("N", title or "")


def _safe_name(name: str) -> str:
    """Sanitize swarm names for use in filenames."""
    safe = _SAFE_NAME_RE.sub("-", name or "unnamed-swarm").strip("-")
    return safe or "unnamed-swarm"


def _build_agent_name_lookup(report: SwarmReport) -> dict[str, str]:
    """Resolve agent_id (UUID) → agent_name from the report's score map."""
    lookup: dict[str, str] = {}
    for aid, score in (report.agent_scores or {}).items():
        name = getattr(score, "agent_name", None)
        if name:
            lookup[aid] = name
    return lookup


def _stable_finding_key(
    swarm_name: str,
    finding: Any,
    name_lookup: dict[str, str],
) -> str:
    """Hash identity for a finding that survives UUID regeneration.

    Identity uses (test_name + primary agent NAME + edge-pair NAME if any +
    normalised title template). Only the first two affected agents enter the
    hash — for cascade/SPOF findings the downstream set grows as the swarm
    grows, but the SPOF itself is the stable identifier.
    """
    raw_agents = getattr(finding, "affected_agents", None) or []
    # Use only the primary (first) affected agent — for SPOF/cascade findings
    # the downstream list isn't deterministically ordered, but the primary
    # subject (the SPOF, the validator, the slow source) is. The normalised
    # title already carries the edge target name for edge findings.
    primary_name = name_lookup.get(raw_agents[0], raw_agents[0]) if raw_agents else ""
    title_template = _normalize_title(finding.title)
    raw = f"{swarm_name}:{finding.test_name}:{title_template}:{primary_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _snapshot_from_report(report: SwarmReport) -> dict[str, Any]:
    """Build the compact dict written to disk for a single run."""
    severity_summary: dict[str, int] = {s.value: 0 for s in Severity}
    finding_records: list[dict[str, Any]] = []
    name_lookup = _build_agent_name_lookup(report)
    for finding in report.all_findings:
        sev = finding.severity.value
        severity_summary[sev] += 1
        finding_records.append(
            {
                "finding_id": _stable_finding_key(report.swarm_name, finding, name_lookup),
                "test_name": finding.test_name,
                "severity": sev,
                "title": finding.title,
                # Persist resolved names, not UUIDs, so a snapshot is portable
                # across runs and human-readable in the history JSON.
                "affected_agents": [name_lookup.get(aid, aid) for aid in finding.affected_agents],
            }
        )

    test_results_summary = [
        {
            "test_name": r.test_name,
            "status": r.status.value,
            "findings_count": len(r.findings),
        }
        for r in report.test_results
    ]

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "iso_timestamp": datetime.now(timezone.utc).isoformat(),
        "swarm_name": report.swarm_name,
        "framework": report.framework,
        "swarm_score": report.swarm_score,
        "risk_score": report.risk_score,
        "total_findings": len(report.all_findings),
        "severity_summary": severity_summary,
        "agent_count": report.agent_count,
        "edge_count": report.edge_count,
        "test_results": test_results_summary,
        "findings": finding_records,
    }


class HistoryStore:
    """Persist swarm-test snapshots to disk and compute trend comparisons."""

    def __init__(self, history_dir: str | Path = ".swarmtest-history") -> None:
        self.history_dir = Path(history_dir)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, report: SwarmReport) -> str:
        """Persist a compact snapshot of ``report`` to disk and return its path."""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        snapshot = _snapshot_from_report(report)
        filename = f"{snapshot['timestamp']}_{_safe_name(report.swarm_name)}.json"
        path = self.history_dir / filename
        # If a snapshot already exists with the same second-precision timestamp,
        # append a numeric suffix so we never silently overwrite a prior run.
        counter = 1
        while path.exists():
            stem = f"{snapshot['timestamp']}_{_safe_name(report.swarm_name)}-{counter}"
            path = self.history_dir / f"{stem}.json"
            counter += 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, default=str)
        return str(path)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _iter_entries(self, swarm_name: str | None = None) -> list[tuple[str, Path]]:
        """Return ``(timestamp, path)`` pairs sorted newest-first."""
        if not self.history_dir.is_dir():
            return []
        target = _safe_name(swarm_name) if swarm_name else None
        entries: list[tuple[str, Path]] = []
        for path in self.history_dir.glob("*.json"):
            match = _HISTORY_FILE_RE.match(path.name)
            if not match:
                continue
            if target is not None and match.group("name") != target:
                continue
            entries.append((match.group("ts"), path))
        entries.sort(key=lambda pair: pair[0], reverse=True)
        return entries

    def load_recent(
        self,
        n: int = 5,
        swarm_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the last ``n`` history entries, newest first."""
        entries = self._iter_entries(swarm_name=swarm_name)[:n]
        loaded: list[dict[str, Any]] = []
        for _ts, path in entries:
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded.append(json.load(fh))
            except (OSError, json.JSONDecodeError):
                continue
        return loaded

    def get_previous(self, swarm_name: str | None = None) -> dict[str, Any] | None:
        """Return the single most recent prior run, or ``None`` if none exists."""
        recent = self.load_recent(n=1, swarm_name=swarm_name)
        return recent[0] if recent else None

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def compare_to_previous(self, current_report: SwarmReport) -> dict[str, Any]:
        """Diff the current report against the most recent prior run."""
        previous = self.get_previous(swarm_name=current_report.swarm_name)
        if previous is None:
            return {"first_run": True}

        current_snapshot = _snapshot_from_report(current_report)
        current_findings = {f["finding_id"]: f for f in current_snapshot["findings"]}
        previous_findings = {f["finding_id"]: f for f in previous.get("findings", [])}

        current_score = current_snapshot["swarm_score"]
        previous_score = previous.get("swarm_score", current_score)
        delta = current_score - previous_score

        if abs(delta) <= _STABLE_TREND_BAND:
            trend = "stable"
        elif delta > 0:
            trend = "improving"
        else:
            trend = "declining"

        # Pull the most recent N scores (current + history) for sparkline display.
        recent_scores: list[int] = [
            entry.get("swarm_score", 0)
            for entry in reversed(self.load_recent(n=5, swarm_name=current_report.swarm_name))
        ]
        recent_scores.append(current_score)

        # Regressions: findings present in both runs whose severity got worse.
        regressed: list[dict[str, Any]] = []
        for fid, current_finding in current_findings.items():
            prev_finding = previous_findings.get(fid)
            if prev_finding is None:
                continue
            try:
                prev_idx = _SEVERITY_ORDER.index(prev_finding["severity"])
                cur_idx = _SEVERITY_ORDER.index(current_finding["severity"])
            except ValueError:
                continue
            if cur_idx < prev_idx:
                regressed.append(
                    {
                        "finding_id": fid,
                        "title": current_finding["title"],
                        "test_name": current_finding["test_name"],
                        "from_severity": prev_finding["severity"],
                        "to_severity": current_finding["severity"],
                    }
                )

        new_findings = [
            {
                "finding_id": fid,
                "title": f["title"],
                "test_name": f["test_name"],
                "severity": f["severity"],
            }
            for fid, f in current_findings.items()
            if fid not in previous_findings
        ]
        resolved_findings = [
            {
                "finding_id": fid,
                "title": f["title"],
                "test_name": f["test_name"],
                "severity": f["severity"],
            }
            for fid, f in previous_findings.items()
            if fid not in current_findings
        ]

        return {
            "first_run": False,
            "previous_timestamp": previous.get("timestamp"),
            "previous_score": previous_score,
            "current_score": current_score,
            "swarm_score_delta": delta,
            "trend": trend,
            "recent_scores": recent_scores,
            "new_findings": new_findings,
            "resolved_findings": resolved_findings,
            "regressed": regressed,
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune(self, keep: int = 50) -> int:
        """Keep only the most recent ``keep`` entries per swarm; return deletions."""
        if keep < 0:
            raise ValueError("keep must be >= 0")
        if not self.history_dir.is_dir():
            return 0

        grouped: dict[str, list[tuple[str, Path]]] = defaultdict(list)
        for path in self.history_dir.glob("*.json"):
            match = _HISTORY_FILE_RE.match(path.name)
            if not match:
                continue
            grouped[match.group("name")].append((match.group("ts"), path))

        deleted = 0
        for entries in grouped.values():
            entries.sort(key=lambda pair: pair[0], reverse=True)
            for _ts, path in entries[keep:]:
                try:
                    path.unlink()
                    deleted += 1
                except OSError:
                    continue
        return deleted

    def clear(self) -> int:
        """Delete every snapshot. Returns the count removed."""
        if not self.history_dir.is_dir():
            return 0
        count = 0
        for path in self.history_dir.glob("*.json"):
            if _HISTORY_FILE_RE.match(path.name):
                try:
                    path.unlink()
                    count += 1
                except OSError:
                    continue
        # Drop the directory if it's now empty so a fresh run starts clean.
        try:
            if not any(self.history_dir.iterdir()):
                shutil.rmtree(self.history_dir, ignore_errors=True)
        except OSError:
            pass
        return count
