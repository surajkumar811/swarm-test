"""HTML reporter — modern dark-themed interactive dashboard with D3 visualisations."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from jinja2 import BaseLoader, Environment, select_autoescape

from swarm_test.core.models import Severity, SwarmReport, redundancy_level

_SEVERITY_COLORS_HEX = {
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#ca8a04",
    "low": "#2563eb",
    "info": "#6b7280",
}

_LEVEL_COLOR = {
    "EXCELLENT": "#22c55e",
    "GOOD": "#84cc16",
    "NEEDS IMPROVEMENT": "#eab308",
    "AT RISK": "#f97316",
    "CRITICAL": "#ef4444",
}


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>swarm-test Reliability Report — {{ report.swarm_name }}</title>
<style>
  :root {
    --bg: #0b1020;
    --bg-elev: #131a2e;
    --card: #1a2238;
    --card-2: #222b46;
    --border: #2d3656;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #6366f1;
    --accent-2: #22d3ee;
    --critical: #ef4444;
    --high:     #f97316;
    --medium:   #eab308;
    --low:      #3b82f6;
    --info:     #6b7280;
    --pass:     #22c55e;
    --fail:     #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.5;
    padding-bottom: 4rem;
  }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code, pre {
    font-family: "SF Mono", Menlo, Consolas, monospace;
    background: rgba(255,255,255,0.05);
    padding: 0.1rem 0.35rem;
    border-radius: 4px;
    font-size: 0.85em;
  }

  /* HEADER ---------------------------------------------------------------- */
  header.report-header {
    padding: 2.5rem 2rem 1.75rem;
    background: linear-gradient(180deg, #0d1428 0%, #0b1020 100%);
    border-bottom: 1px solid var(--border);
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 1.5rem;
    align-items: center;
  }
  header.report-header h1 {
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-bottom: 0.5rem;
  }
  header.report-header .meta {
    color: var(--muted);
    font-size: 0.85rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.25rem;
  }
  header.report-header .meta b { color: var(--text); font-weight: 600; }
  .gauge-wrap { text-align: center; }
  .gauge {
    width: 130px;
    height: 130px;
    position: relative;
  }
  .gauge svg { transform: rotate(-90deg); }
  .gauge .gauge-text {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .gauge .gauge-score { font-size: 2.2rem; font-weight: 700; }
  .gauge .gauge-out  { color: var(--muted); font-size: 0.7rem; letter-spacing: 0.1em; }
  .cert-badge {
    margin-top: 0.6rem;
    display: inline-block;
    padding: 0.35rem 0.85rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  /* NAV ------------------------------------------------------------------- */
  nav.report-nav {
    position: sticky;
    top: 0;
    z-index: 50;
    background: rgba(11,16,32,0.92);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
    padding: 0.6rem 2rem;
    display: flex;
    gap: 1rem;
    overflow-x: auto;
  }
  nav.report-nav a {
    color: var(--muted);
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.35rem 0.75rem;
    border-radius: 6px;
    border: 1px solid transparent;
    transition: all 0.15s;
    white-space: nowrap;
  }
  nav.report-nav a:hover {
    color: var(--text);
    background: var(--card);
    border-color: var(--border);
    text-decoration: none;
  }

  /* SECTIONS -------------------------------------------------------------- */
  main { padding: 2rem; max-width: 1400px; margin: 0 auto; }
  section { margin-bottom: 3rem; }
  .section-title {
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 1rem;
    border-left: 3px solid var(--accent);
    padding-left: 0.75rem;
  }

  /* OVERVIEW CARDS -------------------------------------------------------- */
  .test-card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 1rem;
  }
  .test-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 0.75rem;
    padding: 1rem 1.1rem;
    cursor: pointer;
    transition: transform 0.15s, border-color 0.15s;
    border-left: 4px solid var(--border);
  }
  .test-card:hover { transform: translateY(-2px); border-color: var(--accent); }
  .test-card.passed { border-left-color: var(--pass); }
  .test-card.failed { border-left-color: var(--fail); }
  .test-card.error  { border-left-color: var(--high); }
  .test-card .test-name {
    font-weight: 600;
    margin-bottom: 0.35rem;
    word-break: break-word;
  }
  .test-card .test-meta {
    color: var(--muted);
    font-size: 0.8rem;
    display: flex;
    justify-content: space-between;
  }

  /* GRAPH ----------------------------------------------------------------- */
  .graph-container {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 0.75rem;
    height: 560px;
    position: relative;
    overflow: hidden;
  }
  .graph-container svg { width: 100%; height: 100%; }
  .legend {
    position: absolute;
    bottom: 0.75rem;
    left: 0.75rem;
    background: rgba(11,16,32,0.85);
    border: 1px solid var(--border);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    font-size: 0.75rem;
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .legend .dot {
    display: inline-block;
    width: 10px; height: 10px;
    border-radius: 50%;
    margin-right: 4px;
    vertical-align: middle;
  }
  .tooltip {
    position: absolute;
    background: #0b1020;
    border: 1px solid var(--accent);
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    font-size: 0.8rem;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s;
    z-index: 100;
    max-width: 260px;
  }
  @keyframes spofPulse {
    0%   { stroke: #ef4444; stroke-width: 2; }
    50%  { stroke: #fca5a5; stroke-width: 5; }
    100% { stroke: #ef4444; stroke-width: 2; }
  }
  circle.spof { animation: spofPulse 1.6s infinite; }

  /* HEATMAP --------------------------------------------------------------- */
  .heatmap-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 0.75rem; padding: 1rem; overflow: auto; }
  .heatmap-table { border-collapse: collapse; font-size: 0.75rem; }
  .heatmap-table th, .heatmap-table td {
    border: 1px solid #1a2138;
    padding: 0;
    text-align: center;
    min-width: 32px;
    height: 32px;
  }
  .heatmap-table th {
    background: var(--card-2);
    color: var(--muted);
    font-weight: 600;
    padding: 0.35rem 0.5rem;
    white-space: nowrap;
  }
  .heatmap-table th.row-head { text-align: right; }
  .heatmap-table td.cell {
    position: relative;
    cursor: pointer;
    transition: outline 0.15s;
  }
  .heatmap-table td.cell:hover { outline: 2px solid var(--accent-2); }
  .heatmap-table td.cell-empty { background: transparent; }
  .heatmap-table td.diag { background: #0c1124; }

  /* TABLES ---------------------------------------------------------------- */
  .data-table-wrap {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 0.75rem;
    overflow: hidden;
  }
  table.data {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  table.data thead th {
    background: var(--card-2);
    color: var(--muted);
    text-align: left;
    padding: 0.65rem 0.9rem;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    user-select: none;
  }
  table.data thead th:hover { color: var(--text); }
  table.data tbody td {
    padding: 0.6rem 0.9rem;
    border-bottom: 1px solid var(--border);
  }
  table.data tbody tr:hover { background: rgba(99,102,241,0.06); }
  table.data tbody tr.spof-row { background: rgba(239,68,68,0.08); }
  table.data tbody tr.expanded-detail td {
    background: var(--card-2);
    color: var(--muted);
    font-size: 0.8rem;
  }
  .score-bar-wrap {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }
  .score-bar {
    flex: 1;
    height: 6px;
    border-radius: 3px;
    background: var(--border);
    overflow: hidden;
    min-width: 80px;
  }
  .score-bar > span {
    display: block;
    height: 100%;
    transition: width 0.3s;
  }
  .badge {
    display: inline-block;
    padding: 0.18rem 0.55rem;
    border-radius: 9999px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .badge-critical { background: rgba(239,68,68,0.15); color: var(--critical); }
  .badge-high     { background: rgba(249,115,22,0.15); color: var(--high); }
  .badge-medium   { background: rgba(234,179,8,0.15); color: var(--medium); }
  .badge-low      { background: rgba(59,130,246,0.15); color: var(--low); }
  .badge-info     { background: rgba(107,114,128,0.15); color: var(--info); }
  .badge-passed   { background: rgba(34,197,94,0.15); color: var(--pass); }
  .badge-failed   { background: rgba(239,68,68,0.15); color: var(--fail); }
  .badge-error    { background: rgba(249,115,22,0.15); color: var(--high); }
  .badge-spof     { background: rgba(239,68,68,0.18); color: var(--critical); }
  .badge-role-orchestrator { background: rgba(34,211,238,0.15); color: var(--accent-2); }
  .badge-role-aggregator   { background: rgba(34,211,238,0.15); color: var(--accent-2); }
  .badge-role-gateway      { background: rgba(99,102,241,0.18); color: var(--accent); }
  .badge-role-validator    { background: rgba(234,179,8,0.15); color: var(--medium); }
  .badge-role-router       { background: rgba(148,163,184,0.18); color: var(--muted); }
  .badge-role-worker       { background: rgba(148,163,184,0.12); color: var(--text); }
  .badge-role-monitor      { background: rgba(107,114,128,0.18); color: var(--muted); }
  .badge-role-unknown      { background: rgba(107,114,128,0.18); color: var(--muted); }

  /* FINDINGS -------------------------------------------------------------- */
  .filter-bar {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }
  .filter-bar button {
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 0.35rem 0.85rem;
    border-radius: 9999px;
    cursor: pointer;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .filter-bar button.active {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
  }
  .finding {
    background: var(--card);
    border: 1px solid var(--border);
    border-left: 4px solid;
    border-radius: 0.5rem;
    margin-bottom: 0.6rem;
    overflow: hidden;
  }
  .finding.critical { border-left-color: var(--critical); }
  .finding.high     { border-left-color: var(--high); }
  .finding.medium   { border-left-color: var(--medium); }
  .finding.low      { border-left-color: var(--low); }
  .finding.info     { border-left-color: var(--info); }
  .finding summary {
    list-style: none;
    cursor: pointer;
    padding: 0.85rem 1rem;
    display: flex;
    gap: 0.6rem;
    align-items: center;
  }
  .finding summary::-webkit-details-marker { display: none; }
  .finding summary::before {
    content: '▶';
    color: var(--muted);
    font-size: 0.7rem;
    transition: transform 0.2s;
  }
  .finding[open] summary::before { transform: rotate(90deg); }
  .finding .finding-title { font-weight: 600; flex: 1; }
  .finding .finding-test {
    color: var(--muted);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .finding-body {
    padding: 0 1rem 1rem;
    color: var(--text);
  }
  .finding-body .desc { color: var(--muted); font-size: 0.88rem; margin: 0.5rem 0 0.75rem; }
  .finding-body .affected {
    font-size: 0.78rem;
    color: var(--muted);
    margin-bottom: 0.5rem;
  }
  .finding-body .affected b { color: var(--text); }
  .finding-body .remediation {
    margin-top: 0.6rem;
    padding: 0.6rem 0.85rem;
    background: rgba(34,211,238,0.08);
    border-left: 3px solid var(--accent-2);
    border-radius: 0 6px 6px 0;
    color: var(--text);
    font-size: 0.88rem;
  }
  .finding-body .remediation::before {
    content: '→ ';
    color: var(--accent-2);
    font-weight: 700;
  }

  /* FOOTER ---------------------------------------------------------------- */
  footer.report-footer {
    margin-top: 3rem;
    padding: 1.5rem 2rem;
    color: var(--muted);
    font-size: 0.8rem;
    text-align: center;
    border-top: 1px solid var(--border);
  }

  .hint { color: var(--muted); font-size: 0.8rem; margin-bottom: 0.75rem; }
  .pill {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    background: var(--card-2);
    color: var(--muted);
    font-size: 0.7rem;
  }
  .empty-state {
    background: var(--card);
    border: 1px dashed var(--border);
    border-radius: 0.5rem;
    padding: 1.5rem;
    text-align: center;
    color: var(--muted);
    font-size: 0.9rem;
  }
</style>
</head>
<body>

<header class="report-header">
  <div>
    <h1>swarm-test Reliability Report</h1>
    <div class="meta">
      <span><b>{{ report.swarm_name }}</b></span>
      <span>Framework: <b>{{ report.framework }}</b></span>
      <span>Agents: <b>{{ report.agent_count }}</b></span>
      <span>Edges: <b>{{ report.edge_count }}</b></span>
      <span>Generated: <b>{{ generated_at }}</b></span>
    </div>
  </div>
  <div class="gauge-wrap">
    <div class="gauge">
      <svg width="130" height="130" viewBox="0 0 130 130">
        <circle cx="65" cy="65" r="55" stroke="#1a2238" stroke-width="12" fill="none"/>
        <circle cx="65" cy="65" r="55"
                stroke="{{ level_color }}" stroke-width="12" fill="none"
                stroke-linecap="round"
                stroke-dasharray="{{ gauge_dash }} 999"/>
      </svg>
      <div class="gauge-text">
        <div class="gauge-score" style="color: {{ level_color }};">{{ report.swarm_score }}</div>
        <div class="gauge-out">/ 100</div>
      </div>
    </div>
    <div class="cert-badge" style="background: {{ level_color }}1f; color: {{ level_color }};">
      {{ certification_level }}
    </div>
  </div>
</header>

<nav class="report-nav">
  <a href="#overview">Overview</a>
  <a href="#agent-graph">Agent Graph</a>
  <a href="#heatmap">Heatmap</a>
  <a href="#health">Health Scores</a>
  <a href="#redundancy">Redundancy</a>
  <a href="#findings">Findings</a>
</nav>

<main>

<section id="overview">
  <h2 class="section-title">Overview · Test Results</h2>
  <div class="hint">{{ report.passed_count }}/{{ report.test_results | length }} tests passed · click a card to jump to its findings</div>
  <div class="test-card-grid">
    {% for result in report.test_results %}
    <div class="test-card {{ result.status.value }}" data-test="{{ result.test_name }}" onclick="filterByTest('{{ result.test_name }}')">
      <div class="test-name">{{ result.test_name }}</div>
      <div class="test-meta">
        <span><span class="badge badge-{{ result.status.value }}">{{ result.status.value.upper() }}</span></span>
        <span>{{ result.findings | length }} finding{{ '' if result.findings | length == 1 else 's' }}</span>
      </div>
    </div>
    {% endfor %}
  </div>
</section>

<section id="agent-graph">
  <h2 class="section-title">Agent Interaction Graph</h2>
  <div class="hint">Drag nodes to reposition · scroll to zoom · click a node to highlight its edges · red pulse = single point of failure</div>
  <div class="graph-container" id="graph-container">
    <div class="legend">
      <span><span class="dot" style="background: var(--pass);"></span>Healthy (≥70)</span>
      <span><span class="dot" style="background: var(--medium);"></span>Moderate (40-69)</span>
      <span><span class="dot" style="background: var(--critical);"></span>Unhealthy (&lt;40)</span>
      <span><span class="dot" style="background: var(--accent-2);"></span>Edge thickness = interactions</span>
    </div>
    <div class="tooltip" id="tooltip-graph"></div>
  </div>
</section>

<section id="heatmap">
  <h2 class="section-title">Interaction Heatmap</h2>
  <div class="hint">Source agent (rows) → target agent (columns). Darker = more interactions. Red overlay = edge has findings.</div>
  {% if heatmap_agents and heatmap_agents | length > 0 %}
  <div class="heatmap-wrap">
    <table class="heatmap-table">
      <thead>
        <tr>
          <th></th>
          {% for col in heatmap_agents %}
          <th title="{{ col.name }}">{{ col.short }}</th>
          {% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for row in heatmap_agents %}
        <tr>
          <th class="row-head" title="{{ row.name }}">{{ row.name }}</th>
          {% for col in heatmap_agents %}
            {% set cell = heatmap_grid[row.id][col.id] %}
            {% if row.id == col.id %}
              <td class="diag" title="—"></td>
            {% elif cell.count == 0 %}
              <td class="cell cell-empty" data-src="{{ row.name }}" data-dst="{{ col.name }}" data-count="0" data-findings="{{ cell.findings_count }}"></td>
            {% else %}
              <td class="cell"
                  style="background: {{ cell.color }};"
                  data-src="{{ row.name }}" data-dst="{{ col.name }}"
                  data-count="{{ cell.count }}" data-findings="{{ cell.findings_count }}"
                  onclick="filterByEdge('{{ row.name }}', '{{ col.name }}')">
              </td>
            {% endif %}
          {% endfor %}
        </tr>
        {% endfor %}
      </tbody>
    </table>
    <div class="tooltip" id="tooltip-heatmap"></div>
  </div>
  {% else %}
  <div class="empty-state">No interaction events recorded — heatmap is empty.</div>
  {% endif %}
</section>

<section id="health">
  <h2 class="section-title">Agent Health Scores</h2>
  {% if agent_scores %}
  <div class="data-table-wrap">
    <table class="data" id="health-table">
      <thead>
        <tr>
          <th data-sort="text">Agent</th>
          <th data-sort="text">Inferred Role</th>
          <th data-sort="num">Health Score</th>
          <th data-sort="text">Status</th>
          <th data-sort="text">Details</th>
        </tr>
      </thead>
      <tbody>
        {% for hs in agent_scores %}
        {% set role_info = agent_roles_lookup.get(hs.agent_id, {}) %}
        <tr class="expandable" data-agent-id="{{ hs.agent_id }}" onclick="toggleAgentDetail(this)">
          <td><b>{{ hs.agent_name }}</b> <span class="pill">{{ hs.role }}</span></td>
          <td>
            <span class="badge badge-role-{{ (role_info.role or 'UNKNOWN') | lower }}">{{ role_info.role or 'UNKNOWN' }}</span>
            <span class="pill">{{ ((role_info.confidence or 0.0) * 100) | round(0, 'floor') | int }}%</span>
          </td>
          <td>
            <div class="score-bar-wrap">
              <span style="min-width:46px; color:{{ health_color(hs.score) }};"><b>{{ hs.score }}</b>/100</span>
              <div class="score-bar"><span style="width: {{ hs.score }}%; background: {{ health_color(hs.score) }};"></span></div>
            </div>
          </td>
          <td>{{ hs.status_label }}</td>
          <td>{{ hs.reasons | join(', ') if hs.reasons else '—' }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty-state">No agent health data available.</div>
  {% endif %}
</section>

<section id="redundancy">
  <h2 class="section-title">Agent Redundancy</h2>
  {% if redundancy_rows %}
  <div class="data-table-wrap">
    <table class="data" id="redundancy-table">
      <thead>
        <tr>
          <th data-sort="text">Agent</th>
          <th data-sort="num">Redundancy Score</th>
          <th data-sort="text">Level</th>
          <th data-sort="text">Risk</th>
        </tr>
      </thead>
      <tbody>
        {% for r in redundancy_rows %}
        <tr class="{{ 'spof-row' if r.is_spof else '' }}">
          <td><b>{{ r.name }}</b></td>
          <td>
            <div class="score-bar-wrap">
              <span style="min-width:46px; color:{{ redundancy_color(r.score) }};"><b>{{ '%.0f' % r.score }}</b>/100</span>
              <div class="score-bar"><span style="width: {{ r.score }}%; background: {{ redundancy_color(r.score) }};"></span></div>
            </div>
          </td>
          <td>{{ r.level }}</td>
          <td>
            {% if r.is_spof %}<span class="badge badge-spof">SPOF</span>
            {% elif r.score <= 60 %}<span class="badge badge-medium">Monitor</span>
            {% else %}<span class="badge badge-passed">Safe</span>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty-state">No redundancy scores available.</div>
  {% endif %}
</section>

<section id="findings">
  <h2 class="section-title">Findings ({{ all_findings | length }})</h2>
  <div class="filter-bar" id="filter-bar">
    <button data-filter="all" class="active">All ({{ all_findings | length }})</button>
    {% for sev_key in ['critical', 'high', 'medium', 'low', 'info'] %}
      {% set count = severity_counts.get(sev_key, 0) %}
      {% if count > 0 %}
      <button data-filter="{{ sev_key }}">{{ sev_key.upper() }} ({{ count }})</button>
      {% endif %}
    {% endfor %}
  </div>
  {% if all_findings %}
  <div id="findings-list">
    {% for finding in all_findings %}
    <details class="finding {{ finding.severity.value }}" data-severity="{{ finding.severity.value }}" data-test="{{ finding.test_name }}">
      <summary>
        <span class="badge badge-{{ finding.severity.value }}">{{ finding.severity.value.upper() }}</span>
        <span class="finding-title">{{ finding.title }}</span>
        <span class="finding-test">{{ finding.test_name }}</span>
      </summary>
      <div class="finding-body">
        <div class="desc">{{ finding.description }}</div>
        {% if finding.affected_agents %}
        <div class="affected"><b>Affected:</b>
          {% for aid in finding.affected_agents %}
            <span class="pill">{{ agent_name_lookup.get(aid, aid) }}</span>
          {% endfor %}
        </div>
        {% endif %}
        {% if finding.remediation %}
        <div class="remediation">{{ finding.remediation }}</div>
        {% endif %}
      </div>
    </details>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty-state" style="color: var(--pass);">No findings — every test passed cleanly.</div>
  {% endif %}
</section>

</main>

<footer class="report-footer">
  Generated by <b>swarm-test v{{ version }}</b> ·
  <a href="https://github.com/surajkumar811/swarm-test">github.com/surajkumar811/swarm-test</a> ·
  {{ generated_at }}
</footer>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
"use strict";

const REPORT_DATA = {{ js_payload | safe }};

// ---------- Findings filter & test-card linking ----------------------------
const filterBar = document.getElementById('filter-bar');
if (filterBar) {
  filterBar.addEventListener('click', (e) => {
    if (e.target.tagName !== 'BUTTON') return;
    const filter = e.target.getAttribute('data-filter');
    filterBar.querySelectorAll('button').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    document.querySelectorAll('#findings-list .finding').forEach(f => {
      if (filter === 'all' || f.getAttribute('data-severity') === filter) {
        f.style.display = '';
      } else {
        f.style.display = 'none';
      }
    });
  });
}

function filterByTest(testName) {
  document.getElementById('findings').scrollIntoView({behavior: 'smooth'});
  document.querySelectorAll('#findings-list .finding').forEach(f => {
    f.style.display = f.getAttribute('data-test') === testName ? '' : 'none';
    if (f.getAttribute('data-test') === testName) f.setAttribute('open', '');
  });
  filterBar.querySelectorAll('button').forEach(b => b.classList.remove('active'));
}

function filterByEdge(src, dst) {
  document.getElementById('findings').scrollIntoView({behavior: 'smooth'});
  document.querySelectorAll('#findings-list .finding').forEach(f => {
    const desc = f.innerText;
    const visible = desc.includes(src) && desc.includes(dst);
    f.style.display = visible ? '' : 'none';
    if (visible) f.setAttribute('open', '');
  });
}

// ---------- Sortable tables ------------------------------------------------
document.querySelectorAll('table.data thead th').forEach((th, idx) => {
  th.addEventListener('click', () => sortTable(th, idx));
});
function sortTable(th, idx) {
  const table = th.closest('table');
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const asc = !(th.dataset.sortDir === 'asc');
  table.querySelectorAll('th').forEach(t => delete t.dataset.sortDir);
  th.dataset.sortDir = asc ? 'asc' : 'desc';
  const numeric = th.dataset.sort === 'num';
  rows.sort((a, b) => {
    const av = a.children[idx].innerText.trim();
    const bv = b.children[idx].innerText.trim();
    if (numeric) {
      const an = parseFloat(av.replace(/[^\d.\-]/g, ''));
      const bn = parseFloat(bv.replace(/[^\d.\-]/g, ''));
      return asc ? an - bn : bn - an;
    }
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(r => tbody.appendChild(r));
}

// ---------- Expandable agent rows ------------------------------------------
function toggleAgentDetail(row) {
  const agentId = row.getAttribute('data-agent-id');
  const next = row.nextElementSibling;
  if (next && next.classList.contains('expanded-detail')) {
    next.remove();
    return;
  }
  const findings = (REPORT_DATA.findings || []).filter(f =>
    (f.affected_agents || []).includes(agentId)
  );
  const detail = document.createElement('tr');
  detail.className = 'expanded-detail';
  const td = document.createElement('td');
  td.colSpan = row.children.length;
  if (findings.length === 0) {
    td.textContent = 'No findings reference this agent.';
  } else {
    td.innerHTML = '<b>' + findings.length + ' finding(s) for this agent:</b><br>' +
      findings.map(f =>
        '<div style="margin-top:0.35rem;">· <span class="badge badge-' + f.severity + '">'
        + f.severity.toUpperCase() + '</span> '
        + escapeHtml(f.title) + '</div>'
      ).join('');
  }
  detail.appendChild(td);
  row.parentNode.insertBefore(detail, row.nextSibling);
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ---------- Heatmap tooltip ------------------------------------------------
const heatmapTooltip = document.getElementById('tooltip-heatmap');
document.querySelectorAll('.heatmap-table td.cell').forEach(cell => {
  cell.addEventListener('mouseenter', (e) => {
    if (!heatmapTooltip) return;
    const src = cell.dataset.src;
    const dst = cell.dataset.dst;
    const count = cell.dataset.count;
    const findings = cell.dataset.findings;
    heatmapTooltip.innerHTML = '<b>' + escapeHtml(src) + ' → ' + escapeHtml(dst)
      + '</b><br>' + count + ' interactions · ' + findings + ' finding(s)';
    heatmapTooltip.style.opacity = '1';
  });
  cell.addEventListener('mousemove', (e) => {
    if (!heatmapTooltip) return;
    const rect = heatmapTooltip.parentElement.getBoundingClientRect();
    heatmapTooltip.style.left = (e.clientX - rect.left + 12) + 'px';
    heatmapTooltip.style.top  = (e.clientY - rect.top  + 12) + 'px';
  });
  cell.addEventListener('mouseleave', () => {
    if (heatmapTooltip) heatmapTooltip.style.opacity = '0';
  });
});

// ---------- D3 force-directed agent graph ----------------------------------
(function renderGraph() {
  const data = REPORT_DATA.graph || {nodes: [], edges: []};
  if (!data.nodes.length) {
    document.getElementById('graph-container').innerHTML =
      '<div style="padding:2rem;color:var(--muted);text-align:center;">No agents to graph.</div>';
    return;
  }
  const container = document.getElementById('graph-container');
  const tooltip = document.getElementById('tooltip-graph');
  const W = container.clientWidth, H = container.clientHeight;

  const svg = d3.select(container).append('svg').attr('viewBox', '0 0 ' + W + ' ' + H);

  svg.append('defs').append('marker')
    .attr('id', 'arrow')
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 24).attr('refY', 0)
    .attr('markerWidth', 7).attr('markerHeight', 7)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-5L10,0L0,5')
    .attr('fill', '#475569');

  const g = svg.append('g');
  svg.call(d3.zoom().scaleExtent([0.3, 3]).on('zoom', e => g.attr('transform', e.transform)));

  // Aggregate edges by (source, target) for clean force layout
  const edgeAgg = new Map();
  data.edges.forEach(e => {
    const k = e.source + '||' + e.target;
    if (!edgeAgg.has(k)) edgeAgg.set(k, {source: e.source, target: e.target, count: 0});
    edgeAgg.get(k).count += 1;
  });
  const links = Array.from(edgeAgg.values());

  const nodes = data.nodes.map(n => Object.assign({}, n));

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(140))
    .force('charge', d3.forceManyBody().strength(-380))
    .force('center', d3.forceCenter(W/2, H/2))
    .force('collision', d3.forceCollide(48));

  const link = g.append('g').selectAll('line')
    .data(links).join('line')
    .attr('stroke', '#475569')
    .attr('stroke-width', d => Math.min(1 + Math.log2(d.count + 1), 5))
    .attr('opacity', 0.7)
    .attr('marker-end', 'url(#arrow)');

  const nodeG = g.append('g').selectAll('g')
    .data(nodes).join('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; })
    );

  nodeG.append('circle')
    .attr('r', d => 16 + Math.min((d.degree || 0) * 2, 12))
    .attr('fill', d => healthFill(d.health_score))
    .attr('stroke', d => d.is_spof ? '#ef4444' : '#0b1020')
    .attr('stroke-width', d => d.is_spof ? 3 : 2)
    .attr('class', d => d.is_spof ? 'spof' : '');

  nodeG.append('text')
    .attr('text-anchor', 'middle').attr('dy', '0.35em')
    .attr('fill', '#0b1020').attr('font-size', '10px').attr('font-weight', 700)
    .text(d => (d.name || d.id).substring(0, 12));

  nodeG.append('text')
    .attr('text-anchor', 'middle')
    .attr('dy', d => (16 + Math.min((d.degree || 0) * 2, 12)) + 14)
    .attr('fill', '#94a3b8')
    .attr('font-size', '9px')
    .attr('font-weight', 600)
    .text(d => d.classified_role && d.classified_role !== 'UNKNOWN' ? d.classified_role : '');

  function healthFill(s) {
    if (s == null) return '#94a3b8';
    if (s >= 70) return '#22c55e';
    if (s >= 40) return '#eab308';
    return '#ef4444';
  }

  nodeG
    .on('mouseover', (e, d) => {
      tooltip.innerHTML = '<b>' + escapeHtml(d.name || d.id) + '</b><br>'
        + 'Role: ' + escapeHtml(d.role || 'unknown') + '<br>'
        + 'Inferred: ' + escapeHtml(d.classified_role || 'UNKNOWN')
        + ' (' + Math.round((d.role_confidence || 0) * 100) + '%)<br>'
        + 'Health: ' + (d.health_score == null ? '—' : d.health_score + '/100') + '<br>'
        + 'Redundancy: ' + (d.redundancy_score == null ? '—' : Math.round(d.redundancy_score) + '/100')
        + (d.tools && d.tools.length ? '<br>Tools: ' + escapeHtml(d.tools.join(', ')) : '')
        + (d.is_spof ? '<br><span style="color:#ef4444;font-weight:700;">⚠ SPOF</span>' : '');
      tooltip.style.opacity = '1';
    })
    .on('mousemove', e => {
      const rect = container.getBoundingClientRect();
      tooltip.style.left = (e.clientX - rect.left + 12) + 'px';
      tooltip.style.top  = (e.clientY - rect.top  + 12) + 'px';
    })
    .on('mouseout', () => { tooltip.style.opacity = '0'; })
    .on('click', (_e, d) => {
      const connected = new Set();
      links.forEach(l => {
        const s = l.source.id || l.source, t = l.target.id || l.target;
        if (s === d.id || t === d.id) { connected.add(s); connected.add(t); }
      });
      link.attr('opacity', l => (l.source.id === d.id || l.target.id === d.id) ? 1 : 0.07);
      nodeG.attr('opacity', n => connected.has(n.id) ? 1 : 0.25);
      setTimeout(() => {
        link.attr('opacity', 0.7);
        nodeG.attr('opacity', 1);
      }, 2500);
    });

  sim.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeG.attr('transform', d => 'translate(' + d.x + ',' + d.y + ')');
  });
})();
</script>
</body>
</html>
"""


def _health_color(score: int | float) -> str:
    if score is None:
        return "#94a3b8"
    s = float(score)
    if s >= 70:
        return "#22c55e"
    if s >= 40:
        return "#eab308"
    return "#ef4444"


def _redundancy_color(score: float) -> str:
    if score <= 20:
        return "#ef4444"
    if score <= 40:
        return "#eab308"
    if score <= 60:
        return "#94a3b8"
    if score <= 80:
        return "#22c55e"
    return "#16a34a"


def _heatmap_cell_color(count: int, has_findings: bool) -> str:
    if has_findings:
        return "#7f1d1d"
    if count <= 0:
        return "transparent"
    # Light → dark blue scale (cap at 10)
    intensity = min(count, 10) / 10.0
    # Linear interpolation between #1e3a5f (light) and #1e40af (dark)
    r = int(30 + (30 - 30) * intensity)
    g_ = int(58 + (64 - 58) * intensity)
    b = int(95 + (175 - 95) * intensity)
    return f"rgb({r},{g_},{b})"


def _short(name: str, limit: int = 8) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


class HtmlReporter:
    """Renders a SwarmReport as a self-contained interactive HTML dashboard."""

    def render(self, report: SwarmReport, output_path: str = "swarm_report.html") -> str:
        return self._render(report, graph=None, output_path=output_path)

    def render_with_graph(
        self, report: SwarmReport, graph: Any, output_path: str = "swarm_report.html"
    ) -> str:
        """Render with full node/edge data from a live SwarmGraph."""
        return self._render(report, graph=graph, output_path=output_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _render(
        self,
        report: SwarmReport,
        *,
        graph: Any | None,
        output_path: str,
    ) -> str:
        try:
            from swarm_test import __version__ as version_str
        except Exception:
            version_str = "0.3.1"

        # ---- Build node + edge data ---------------------------------
        nodes_raw: list[dict[str, Any]] = []
        edges_raw: list[dict[str, Any]] = []
        spof_ids: set[str] = set()
        degree: dict[str, int] = defaultdict(int)

        if graph is not None:
            nodes_raw = graph.node_data()
            edges_raw = graph.edge_data()
            try:
                spof_ids = set(graph.find_single_points_of_failure())
            except Exception:
                spof_ids = set()
            for e in edges_raw:
                degree[e["source"]] += 1
                degree[e["target"]] += 1
                # Strip datetimes for JSON
                for k, v in list(e.items()):
                    if hasattr(v, "isoformat"):
                        e[k] = v.isoformat()
        else:
            for i in range(report.agent_count):
                nodes_raw.append({"id": f"agent_{i}", "name": f"Agent {i + 1}", "role": "unknown"})

        # Health + redundancy lookups
        agent_health: dict[str, Any] = {
            aid: score for aid, score in (report.agent_scores or {}).items()
        }

        # Findings-by-edge (src, dst) → count for heatmap red overlay
        finding_edge_counts: Counter = Counter()
        for finding in report.all_findings:
            if len(finding.affected_agents) >= 2:
                finding_edge_counts[(finding.affected_agents[0], finding.affected_agents[1])] += 1

        # ---- Enrich nodes for D3 ------------------------------------
        nodes_for_js: list[dict[str, Any]] = []
        agent_name_lookup: dict[str, str] = {}
        agent_roles = report.agent_roles or {}
        for n in nodes_raw:
            nid = str(n.get("id", ""))
            name = str(n.get("name", nid))
            agent_name_lookup[nid] = name
            hs = agent_health.get(nid)
            tools: list[str] = []
            meta = n.get("metadata") or {}
            if isinstance(meta, dict):
                tlist = meta.get("tools")
                if isinstance(tlist, (list, tuple)):
                    tools = [str(t) for t in tlist]
            role_info = agent_roles.get(nid, {})
            nodes_for_js.append(
                {
                    "id": nid,
                    "name": name,
                    "role": n.get("role", "unknown"),
                    "classified_role": role_info.get("role", "UNKNOWN"),
                    "role_confidence": float(role_info.get("confidence", 0.0)),
                    "health_score": getattr(hs, "score", None) if hs else None,
                    "redundancy_score": getattr(hs, "redundancy_score", None) if hs else None,
                    "is_spof": nid in spof_ids,
                    "tools": tools,
                    "degree": degree.get(nid, 0),
                }
            )

        edges_for_js: list[dict[str, Any]] = [
            {
                "source": e["source"],
                "target": e["target"],
                "event_type": e.get("event_type", ""),
            }
            for e in edges_raw
        ]

        # ---- Heatmap (NxN) ------------------------------------------
        # Cap at first 25 agents to keep grid readable
        heatmap_agents = nodes_for_js[:25]
        heatmap_agents_view = [
            {"id": n["id"], "name": n["name"], "short": _short(n["name"], 8)}
            for n in heatmap_agents
        ]

        edge_counts: Counter = Counter()
        for e in edges_raw:
            edge_counts[(e["source"], e["target"])] += 1

        heatmap_grid: dict[str, dict[str, dict[str, Any]]] = {}
        for row in heatmap_agents:
            row_id = row["id"]
            heatmap_grid[row_id] = {}
            for col in heatmap_agents:
                col_id = col["id"]
                count = edge_counts.get((row_id, col_id), 0)
                fcount = finding_edge_counts.get((row_id, col_id), 0)
                heatmap_grid[row_id][col_id] = {
                    "count": count,
                    "findings_count": fcount,
                    "color": _heatmap_cell_color(count, fcount > 0),
                }

        # ---- Health table data (sorted worst → best) ----------------
        agent_scores_sorted = sorted(agent_health.values(), key=lambda s: getattr(s, "score", 0))

        # ---- Redundancy rows ----------------------------------------
        redundancy_rows: list[dict[str, Any]] = []
        for aid, r_score in (report.redundancy_scores or {}).items():
            score_obj = agent_health.get(aid)
            name = getattr(score_obj, "agent_name", aid) if score_obj else aid
            redundancy_rows.append(
                {
                    "agent_id": aid,
                    "name": name,
                    "score": float(r_score),
                    "level": redundancy_level(float(r_score)),
                    "is_spof": float(r_score) < 20 or aid in spof_ids,
                }
            )
        redundancy_rows.sort(key=lambda r: r["score"])

        # ---- Findings: sorted by severity ---------------------------
        severity_order = [
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.LOW,
            Severity.INFO,
        ]
        sorted_findings = sorted(
            report.all_findings, key=lambda f: severity_order.index(f.severity)
        )
        severity_counts = report.severity_counts()

        # ---- JS payload ---------------------------------------------
        js_payload = {
            "graph": {"nodes": nodes_for_js, "edges": edges_for_js},
            "findings": [
                {
                    "title": f.title,
                    "severity": f.severity.value,
                    "test_name": f.test_name,
                    "affected_agents": list(f.affected_agents),
                }
                for f in sorted_findings
            ],
        }

        # ---- Render -------------------------------------------------
        level = report.certification_level
        level_color = _LEVEL_COLOR.get(level, "#94a3b8")
        gauge_dash = round(report.swarm_score / 100.0 * (2 * 3.141592653589793 * 55), 2)

        env = Environment(
            loader=BaseLoader(),
            autoescape=select_autoescape(default=True, default_for_string=True),
        )
        env.filters["tojson"] = lambda v: json.dumps(v, default=str)
        env.globals["health_color"] = _health_color
        env.globals["redundancy_color"] = _redundancy_color
        template = env.from_string(_HTML_TEMPLATE)

        html = template.render(
            report=report,
            all_findings=sorted_findings,
            agent_scores=agent_scores_sorted,
            redundancy_rows=redundancy_rows,
            heatmap_agents=heatmap_agents_view,
            heatmap_grid=heatmap_grid,
            agent_name_lookup=agent_name_lookup,
            agent_roles_lookup=dict(agent_roles),
            severity_counts=severity_counts,
            certification_level=level,
            level_color=level_color,
            gauge_dash=gauge_dash,
            severity_colors=_SEVERITY_COLORS_HEX,
            js_payload=json.dumps(js_payload, default=str),
            version=version_str,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return output_path

    @staticmethod
    def _sorted_scores(report: SwarmReport) -> list[Any]:
        """Backwards-compat helper kept for any external callers."""
        if not report.agent_scores:
            return []
        return sorted(report.agent_scores.values(), key=lambda s: s.score)
