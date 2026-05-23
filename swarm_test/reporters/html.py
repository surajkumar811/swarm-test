"""HTML reporter with D3.js force-directed graph visualization."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from jinja2 import BaseLoader, Environment, select_autoescape

from swarm_test.core.models import SwarmReport

_SEVERITY_COLORS_HEX = {
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#ca8a04",
    "low": "#2563eb",
    "info": "#6b7280",
}

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>SwarmTest Report — {{ report.swarm_name }}</title>
<style>
  :root {
    --bg: #0f172a; --card: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8;
    --critical: #dc2626; --high: #ea580c;
    --medium: #ca8a04; --low: #2563eb; --info: #6b7280;
    --pass: #16a34a; --fail: #dc2626;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', sans-serif; padding: 2rem; }
  h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 0.5rem; }
  .subtitle { color: var(--muted); margin-bottom: 2rem; font-size: 0.9rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 0.75rem; padding: 1.25rem; }
  .card .label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 2rem; font-weight: 700; margin-top: 0.25rem; }
  .risk-low { color: #16a34a; }
  .risk-medium { color: #ca8a04; }
  .risk-high { color: #dc2626; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 2rem; }
  th { background: var(--card); padding: 0.75rem 1rem; text-align: left; font-size: 0.8rem; text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--border); }
  td { padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
  tr:hover td { background: var(--card); }
  .badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }
  .badge-critical { background: #7f1d1d; color: var(--critical); }
  .badge-high     { background: #431407; color: var(--high); }
  .badge-medium   { background: #422006; color: var(--medium); }
  .badge-low      { background: #1e3a5f; color: var(--low); }
  .badge-info     { background: #1e293b; color: var(--info); }
  .badge-passed   { background: #14532d; color: #4ade80; }
  .badge-failed   { background: #7f1d1d; color: #f87171; }
  .badge-error    { background: #7f1d1d; color: #fbbf24; }
  .section-title { font-size: 1.2rem; font-weight: 600; margin: 2rem 0 1rem; border-left: 4px solid #6366f1; padding-left: 0.75rem; }
  #graph-container { background: var(--card); border: 1px solid var(--border); border-radius: 0.75rem; width: 100%; height: 500px; margin-bottom: 2rem; position: relative; overflow: hidden; }
  #graph-container svg { width: 100%; height: 100%; }
  .finding-card { background: var(--card); border: 1px solid var(--border); border-radius: 0.75rem; padding: 1.25rem; margin-bottom: 1rem; border-left: 4px solid; }
  .finding-card.critical { border-left-color: var(--critical); }
  .finding-card.high     { border-left-color: var(--high); }
  .finding-card.medium   { border-left-color: var(--medium); }
  .finding-card.low      { border-left-color: var(--low); }
  .finding-card.info     { border-left-color: var(--info); }
  .finding-title { font-weight: 600; margin-bottom: 0.5rem; }
  .finding-desc { color: var(--muted); font-size: 0.875rem; margin-bottom: 0.5rem; }
  .finding-remediation { font-size: 0.8rem; color: #7dd3fc; margin-top: 0.5rem; }
  .tooltip { position: absolute; background: #0f172a; border: 1px solid #334155; border-radius: 0.5rem; padding: 0.5rem 0.75rem; font-size: 0.8rem; pointer-events: none; opacity: 0; transition: opacity 0.2s; }
</style>
</head>
<body>
<h1>SwarmTest Reliability Report</h1>
<div class="subtitle">
  {{ report.swarm_name }} &mdash; {{ report.framework }} &mdash;
  Generated {{ generated_at }}
</div>

<div class="grid">
  <div class="card">
    <div class="label">Risk Score</div>
    <div class="value {{ risk_class }}">{{ report.risk_score | int }}<span style="font-size:1rem">/100</span></div>
  </div>
  <div class="card">
    <div class="label">Agents</div>
    <div class="value">{{ report.agent_count }}</div>
  </div>
  <div class="card">
    <div class="label">Edges</div>
    <div class="value">{{ report.edge_count }}</div>
  </div>
  <div class="card">
    <div class="label">Tests</div>
    <div class="value">{{ report.passed_count }}<span style="font-size:1rem; color:var(--muted);">/{{ report.test_results | length }}</span></div>
  </div>
  <div class="card">
    <div class="label">Findings</div>
    <div class="value">{{ all_findings | length }}</div>
  </div>
  <div class="card">
    <div class="label">Duration</div>
    <div class="value" style="font-size:1.4rem;">{{ report.total_duration_ms | int }}ms</div>
  </div>
</div>

<div class="section-title">Agent Interaction Graph</div>
<div id="graph-container">
  <div class="tooltip" id="tooltip"></div>
</div>

<div class="section-title">Test Results</div>
<table>
  <thead>
    <tr>
      <th>Test</th><th>Status</th><th>Findings</th><th>Critical</th><th>High</th><th>Duration</th>
    </tr>
  </thead>
  <tbody>
  {% for result in report.test_results %}
    <tr>
      <td>{{ result.test_name }}</td>
      <td><span class="badge badge-{{ result.status.value }}">{{ result.status.value.upper() }}</span></td>
      <td>{{ result.findings | length }}</td>
      <td>{{ result.severity_count().get('critical', 0) }}</td>
      <td>{{ result.severity_count().get('high', 0) }}</td>
      <td>{{ '%.1f' % result.duration_ms }}ms</td>
    </tr>
  {% endfor %}
  </tbody>
</table>

{% if all_findings %}
<div class="section-title">Findings ({{ all_findings | length }})</div>
{% for finding in all_findings %}
<div class="finding-card {{ finding.severity.value }}">
  <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.5rem;">
    <span class="badge badge-{{ finding.severity.value }}">{{ finding.severity.value.upper() }}</span>
    <span style="color:var(--muted); font-size:0.8rem;">{{ finding.test_name }}</span>
  </div>
  <div class="finding-title">{{ finding.title }}</div>
  <div class="finding-desc">{{ finding.description }}</div>
  <div class="finding-remediation">Remediation: {{ finding.remediation }}</div>
</div>
{% endfor %}
{% else %}
<div class="card" style="text-align:center; color:#4ade80; padding:2rem;">
  All tests passed. No findings detected.
</div>
{% endif %}

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const graphData = {{ graph_data | tojson | safe }};
const severityColors = {{ severity_colors | tojson | safe }};

(function() {
  const container = document.getElementById('graph-container');
  const W = container.clientWidth, H = container.clientHeight;
  const tooltip = document.getElementById('tooltip');

  const svg = d3.select('#graph-container').append('svg')
    .attr('width', W).attr('height', H);

  svg.append('defs').append('marker')
    .attr('id', 'arrow')
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 20).attr('refY', 0)
    .attr('markerWidth', 6).attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-5L10,0L0,5')
    .attr('fill', '#475569');

  const g = svg.append('g');

  svg.call(d3.zoom().scaleExtent([0.3, 3]).on('zoom', e => g.attr('transform', e.transform)));

  const nodes = graphData.nodes.map(n => ({...n}));
  const links = graphData.edges.map(e => ({
    source: e.source, target: e.target, event_type: e.event_type
  }));

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(120))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide(40));

  const link = g.append('g').selectAll('line')
    .data(links).join('line')
    .attr('stroke', '#475569').attr('stroke-width', 1.5)
    .attr('marker-end', 'url(#arrow)');

  const nodeGroup = g.append('g').selectAll('g')
    .data(nodes).join('g')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx=null; d.fy=null; })
    );

  nodeGroup.append('circle')
    .attr('r', 22)
    .attr('fill', '#1e293b')
    .attr('stroke', '#6366f1')
    .attr('stroke-width', 2);

  nodeGroup.append('text')
    .attr('text-anchor', 'middle').attr('dy', '0.35em')
    .attr('fill', '#e2e8f0').attr('font-size', '10px')
    .text(d => (d.name || d.id).substring(0, 10));

  nodeGroup.append('title').text(d => `${d.name}\nRole: ${d.role || 'unknown'}`);

  nodeGroup
    .on('mouseover', (e, d) => {
      tooltip.style.opacity = '1';
      const esc = s => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
      tooltip.innerHTML = `<b>${esc(d.name)}</b><br>Role: ${esc(d.role || 'unknown')}<br>ID: ${esc(d.id.substring(0,8))}...`;
    })
    .on('mousemove', e => {
      const rect = container.getBoundingClientRect();
      tooltip.style.left = (e.clientX - rect.left + 10) + 'px';
      tooltip.style.top  = (e.clientY - rect.top  + 10) + 'px';
    })
    .on('mouseout', () => { tooltip.style.opacity = '0'; });

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
  });
})();
</script>
</body>
</html>
"""


class HtmlReporter:
    """Renders a SwarmReport as a self-contained HTML file with D3 graph."""

    def render(self, report: SwarmReport, output_path: str = "swarm_report.html") -> str:
        graph_data = self._build_graph_data(report)
        risk_class = (
            "risk-high"
            if report.risk_score >= 60
            else "risk-medium" if report.risk_score >= 30 else "risk-low"
        )

        env = Environment(
            loader=BaseLoader(), autoescape=select_autoescape(default=True, default_for_string=True)
        )
        env.filters["tojson"] = lambda v: json.dumps(v, default=str)
        template = env.from_string(_HTML_TEMPLATE)

        html = template.render(
            report=report,
            all_findings=report.all_findings,
            graph_data=graph_data,
            severity_colors=_SEVERITY_COLORS_HEX,
            risk_class=risk_class,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        return output_path

    @staticmethod
    def _build_graph_data(report: SwarmReport) -> dict[str, Any]:
        """Build a minimal graph representation for D3."""
        # We need to reconstruct the graph data from the report
        # The probe stores graph in report.graph_metrics; for rendering
        # we use the agent_count and edge_count and reconstruct from metrics.
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # For the HTML report we generate placeholder nodes based on agent_count
        for i in range(report.agent_count):
            nodes.append(
                {
                    "id": f"agent_{i}",
                    "name": f"Agent {i+1}",
                    "role": "unknown",
                }
            )

        return {"nodes": nodes, "edges": edges}

    def attach_graph(self, graph: Any) -> None:
        """Attach the live SwarmGraph for richer visualization."""
        self._graph = graph

    def render_with_graph(
        self, report: SwarmReport, graph: Any, output_path: str = "swarm_report.html"
    ) -> str:
        """Render with full node/edge data from a live SwarmGraph."""
        nodes = graph.node_data()
        edges = graph.edge_data()

        # Convert datetime objects to strings for JSON
        for edge in edges:
            for k, v in list(edge.items()):
                if hasattr(v, "isoformat"):
                    edge[k] = v.isoformat()

        risk_class = (
            "risk-high"
            if report.risk_score >= 60
            else "risk-medium" if report.risk_score >= 30 else "risk-low"
        )

        env = Environment(
            loader=BaseLoader(), autoescape=select_autoescape(default=True, default_for_string=True)
        )
        env.filters["tojson"] = lambda v: json.dumps(v, default=str)
        template = env.from_string(_HTML_TEMPLATE)

        html = template.render(
            report=report,
            all_findings=report.all_findings,
            graph_data={"nodes": nodes, "edges": edges},
            severity_colors=_SEVERITY_COLORS_HEX,
            risk_class=risk_class,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        return output_path
