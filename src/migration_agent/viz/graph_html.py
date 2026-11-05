"""Self-contained HTML visualizer for the knowledge graph (B-12).

``render_graph_html`` returns ONE offline HTML file: graph JSON embedded in a
``<script type="application/json">`` block, force-directed layout and canvas
rendering implemented in vanilla JS. No CDN, no external requests.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from migration_agent.graph.models import Graph
from migration_agent.graph.store import diff_graphs


def _evidence(ev) -> list[dict]:
    return [
        {"file": e.file, "ls": e.line_start, "le": e.line_end, "symbol": e.symbol, "quote": e.quote}
        for e in ev
    ]


def _payload(graph: Graph, previous: Graph | None) -> dict:
    diff_mode = previous is not None
    node_status: dict[str, str] = {}
    edge_status: dict[str, str] = {}
    if diff_mode:
        d = diff_graphs(previous, graph)
        node_status.update({i: "added" for i in d.added_nodes})
        node_status.update({i: "changed" for i in d.changed_nodes})
        node_status.update({i: "removed" for i in d.removed_nodes})
        edge_status.update({i: "added" for i in d.added_edges})
        edge_status.update({i: "changed" for i in d.changed_edges})
        edge_status.update({i: "removed" for i in d.removed_edges})

    def node(n, ghost=False) -> dict:
        return {
            "id": n.id, "type": n.type, "name": n.name, "module": n.module,
            "symbol": n.qualified_symbol, "responsibility": n.responsibility,
            "confidence": n.confidence, "quirk": n.quirk, "bug": n.suspected_bug,
            "evidence": _evidence(n.evidence), "status": node_status.get(n.id, ""),
        }

    def edge(e) -> dict:
        return {
            "id": e.id, "source": e.source, "target": e.target, "type": e.type,
            "confidence": e.confidence, "evidence": _evidence(e.evidence),
            "status": edge_status.get(e.id, ""),
        }

    nodes = [node(n) for n in graph.nodes]
    edges = [edge(e) for e in graph.edges]
    if diff_mode:
        live = {n.id for n in graph.nodes}
        nodes += [node(n) for n in previous.nodes if n.id not in live]
        live_e = {e.id for e in graph.edges}
        edges += [edge(e) for e in previous.edges if e.id not in live_e]
    return {"diff": diff_mode, "version": graph.version, "nodes": nodes, "edges": edges}


def _json_for_script(data: dict) -> str:
    s = json.dumps(data, ensure_ascii=False)
    # Neutralise anything that could terminate the script element or start a comment.
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_graph_html(graph: Graph, *, previous: Graph | None = None, title: str = "") -> str:
    page_title = title or "Relationship graph"
    return (_TEMPLATE
            .replace("__TITLE__", html.escape(page_title))
            .replace("__DATA__", _json_for_script(_payload(graph, previous))))


def write_graph_html(path: str | Path, graph: Graph, previous: Graph | None = None, title: str = "") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_graph_html(graph, previous=previous, title=title), encoding="utf-8")
    return p


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script>
try { var t = localStorage.getItem('lavish-theme'); document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light'); } catch (e) { document.documentElement.setAttribute('data-theme', 'light'); }
</script>
<style>
:root {
  --bg: #f7f8fa; --panel: #ffffff; --text: #1c2430; --muted: #5d6879; --border: #d9dee6;
  --edge: #8a94a6; --edge-strong: #3d4a5f; --accent: #2457d6; --halo: #f7f8fa;
  --added: #138a43; --changed: #c77700; --removed: #cc2f3a; --bug: #cc2f3a; --quirk: #b8860b;
  --c-component: #3b6fd8; --c-endpoint: #178a6a; --c-data_entity: #c2571a; --c-external_dependency: #7a52c7;
  --c-business_rule: #b8860b; --c-config: #5f6b7a; --c-job: #c2387a; --c-integration: #1f8aa8; --c-dead_code: #8f98a6;
}
:root[data-theme="dark"] {
  --bg: #12161d; --panel: #1b212b; --text: #e4e9f1; --muted: #9aa5b8; --border: #2d3644;
  --edge: #6d788c; --edge-strong: #b3bdd0; --accent: #7ba3ff; --halo: #12161d;
  --added: #4cd081; --changed: #f0b04a; --removed: #ff6b75; --bug: #ff6b75; --quirk: #e6c04a;
  --c-component: #6f9bff; --c-endpoint: #3fcfa6; --c-data_entity: #f08a4b; --c-external_dependency: #a98aee;
  --c-business_rule: #e0b84a; --c-config: #9aa5b8; --c-job: #f26fae; --c-integration: #4cc3e0; --c-dead_code: #7d8798;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { background: var(--bg); color: var(--text); font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; display: flex; flex-direction: column; overflow: hidden; }
header { display: flex; gap: 12px; align-items: center; padding: 8px 14px; background: var(--panel); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
header h1 { font-size: 15px; margin: 0; font-weight: 650; }
#stats { color: var(--muted); font-size: 12px; }
header .sp { flex: 1; }
input[type=search] { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 5px 9px; width: 240px; font: inherit; }
input[type=search]:focus, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
button { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 5px 10px; font: inherit; cursor: pointer; }
button:hover { border-color: var(--accent); }
main { flex: 1; position: relative; min-height: 0; display: flex; }
#stage { flex: 1; position: relative; min-width: 0; }
canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
canvas.drag { cursor: grabbing; }
.overlay { position: absolute; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; opacity: .96; }
#legend { top: 10px; left: 10px; max-width: 210px; }
#legend h2, #elegend h2, #dlegend h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 0 0 5px; font-weight: 600; }
.lg { display: flex; align-items: center; gap: 7px; padding: 2px 3px; border-radius: 4px; cursor: pointer; user-select: none; }
.lg:hover { background: var(--bg); }
.lg.off { opacity: .4; text-decoration: line-through; }
.lg .dot { width: 11px; height: 11px; border-radius: 50%; flex: none; }
.lg .n { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
#elegend { bottom: 10px; left: 10px; }
#elegend .er { display: flex; align-items: center; gap: 7px; font-size: 12px; }
#dlegend { bottom: 10px; left: 50%; transform: translateX(-50%); display: none; white-space: nowrap; }
#dlegend .row { display: flex; gap: 14px; }
#dlegend span.k { display: inline-flex; align-items: center; gap: 5px; }
#dlegend i { width: 11px; height: 11px; border-radius: 50%; border: 3px solid; display: inline-block; }
#hint { bottom: 10px; right: 10px; color: var(--muted); font-size: 11px; }
#empty { inset: 0; margin: auto; width: 360px; height: 120px; text-align: center; display: none; padding-top: 28px; }
#empty b { font-size: 15px; }
#panel { width: 360px; background: var(--panel); border-left: 1px solid var(--border); overflow: auto; padding: 14px; display: none; flex: none; }
#panel h2 { margin: 0 0 2px; font-size: 15px; word-break: break-word; }
#panel .id { font-family: ui-monospace, Menlo, monospace; color: var(--muted); font-size: 12px; }
#panel .close { float: right; }
#panel h3 { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 14px 0 5px; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border); font-size: 11px; margin: 0 4px 4px 0; }
.chip.bug { color: var(--bug); border-color: var(--bug); } .chip.quirk { color: var(--quirk); border-color: var(--quirk); }
.chip.added { color: var(--added); border-color: var(--added); } .chip.changed { color: var(--changed); border-color: var(--changed); } .chip.removed { color: var(--removed); border-color: var(--removed); }
.ev { border-left: 3px solid var(--border); padding: 2px 0 2px 8px; margin: 0 0 7px; }
.ev code { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: var(--accent); word-break: break-all; }
.ev pre { margin: 3px 0 0; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; color: var(--text); background: var(--bg); padding: 4px 6px; border-radius: 4px; }
.nb { display: block; width: 100%; text-align: left; margin: 0 0 3px; padding: 3px 8px; }
.nb .et { color: var(--muted); font-size: 11px; margin-right: 4px; }
.muted { color: var(--muted); }
@media (max-width: 700px) { #panel { position: absolute; right: 0; top: 0; bottom: 0; width: 88%; } input[type=search] { width: 150px; } }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span id="stats"></span>
  <span class="sp"></span>
  <input id="q" type="search" placeholder="Search id or name (Enter to zoom)" aria-label="Search nodes">
  <button id="fit" type="button">Fit</button>
  <button id="theme" type="button" aria-label="Toggle light or dark theme">Dark</button>
</header>
<main>
  <div id="stage">
    <canvas id="cv"></canvas>
    <div class="overlay" id="legend"><h2>Node types (click to filter)</h2><div id="lgitems"></div></div>
    <div class="overlay" id="elegend"><h2>Edge types</h2><div id="eitems"></div></div>
    <div class="overlay" id="dlegend"><h2>Changes vs previous</h2><div class="row">
      <span class="k"><i style="border-color:var(--added)"></i>added</span>
      <span class="k"><i style="border-color:var(--changed)"></i>changed</span>
      <span class="k"><i style="border-color:var(--removed);border-style:dashed"></i>removed (ghost)</span></div></div>
    <div class="overlay" id="hint">scroll to zoom, drag to pan, drag nodes</div>
    <div class="overlay" id="empty"><b>No nodes in this graph yet</b><div class="muted">Run an analysis to populate the relationship graph.</div></div>
  </div>
  <aside id="panel" aria-live="polite"></aside>
</main>
<script id="graph-data" type="application/json">__DATA__</script>
<script>
(function () {
'use strict';
var DATA = JSON.parse(document.getElementById('graph-data').textContent);
var TYPES = ['component','endpoint','data_entity','external_dependency','business_rule','config','job','integration','dead_code'];
var DASH = { calls: [], reads: [2,3], writes: [9,3], depends_on: [5,4], publishes: [11,3,2,3], consumes: [2,6], implements: [14,4], configures: [6,2,1,2] };
var root = document.documentElement;
var cv = document.getElementById('cv'), ctx = cv.getContext('2d');
var $ = function (id) { return document.getElementById(id); };
var COL = {};
function readColors() {
  var cs = getComputedStyle(root);
  ['bg','panel','text','muted','border','edge','edge-strong','accent','halo','added','changed','removed','bug','quirk'].forEach(function (k) { COL[k] = cs.getPropertyValue('--' + k).trim(); });
  TYPES.forEach(function (t) { COL[t] = cs.getPropertyValue('--c-' + t).trim(); });
}
function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

// ---------- model ----------
var nodes = DATA.nodes.map(function (n, i) {
  return { d: n, i: i, id: n.id, x: 0, y: 0, vx: 0, vy: 0, deg: 0, r: 8, ghost: n.status === 'removed', pin: false };
});
var byId = {}; nodes.forEach(function (n) { byId[n.id] = n; });
var edges = [];
DATA.edges.forEach(function (e) {
  var s = byId[e.source], t = byId[e.target];
  if (!s || !t) return;
  edges.push({ d: e, s: s, t: t, i: edges.length });
  s.deg++; t.deg++;
});
var adj = {}; nodes.forEach(function (n) { adj[n.id] = { out: [], inn: [] }; });
edges.forEach(function (e) { adj[e.s.id].out.push(e); adj[e.t.id].inn.push(e); });
var N = nodes.length;
var rscale = N > 1000 ? 0.55 : N > 400 ? 0.75 : 1;
nodes.forEach(function (n) { n.r = (6 + 2.6 * Math.sqrt(n.deg)) * rscale; if (n.r > 26) n.r = 26; });
var byDeg = nodes.slice().sort(function (a, b) { return b.deg - a.deg; });

var hidden = {};          // type -> true when filtered out
var selected = null, hover = null, matches = null;
var view = { k: 1, x: 0, y: 0 };
var W = 0, H = 0, dpr = 1;

function visible(n) { return !hidden[n.d.type]; }

// ---------- layout ----------
var alpha = 1, ticks = 0, simOn = false;
nodes.forEach(function (n, i) { var a = i * 2.399963, rr = 38 * Math.sqrt(i + 0.5) * (N > 400 ? 0.8 : 1); n.x = Math.cos(a) * rr; n.y = Math.sin(a) * rr; });
var LINK = N > 400 ? 40 : 110, REP = N > 400 ? 1500 : 11000;
function tick() {
  var i, j, a, b, dx, dy, d2, d, f;
  if (N <= 300) {
    for (i = 0; i < N; i++) { a = nodes[i]; for (j = i + 1; j < N; j++) { b = nodes[j]; rep(a, b); } }
  } else {
    var C = 110, grid = {}, key;
    for (i = 0; i < N; i++) { a = nodes[i]; key = Math.floor(a.x / C) + ',' + Math.floor(a.y / C); (grid[key] || (grid[key] = [])).push(a); }
    for (i = 0; i < N; i++) {
      a = nodes[i]; var cx = Math.floor(a.x / C), cy = Math.floor(a.y / C);
      for (var gx = -1; gx <= 1; gx++) for (var gy = -1; gy <= 1; gy++) {
        var cell = grid[(cx + gx) + ',' + (cy + gy)]; if (!cell) continue;
        for (j = 0; j < cell.length; j++) { b = cell[j]; if (b.i > a.i) rep(a, b); }
      }
    }
  }
  function rep(a, b) {
    var dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
    if (d2 < 0.01) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = dx * dx + dy * dy + 0.01; }
    var d = Math.sqrt(d2), minD = a.r + b.r + 14, f = REP * alpha / d2;
    if (d < minD) f += (minD - d) * 0.5 * alpha;
    if (d > 700) return;
    f /= d; dx *= f; dy *= f;
    a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
  }
  for (i = 0; i < edges.length; i++) {
    var e = edges[i]; a = e.s; b = e.t;
    dx = b.x - a.x; dy = b.y - a.y; d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    var rest = LINK + a.r + b.r; f = (d - rest) * 0.06 * alpha / d;
    dx *= f; dy *= f; a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
  }
  for (i = 0; i < N; i++) {
    a = nodes[i];
    a.vx -= a.x * 0.006 * alpha; a.vy -= a.y * 0.006 * alpha;
    if (a.pin) { a.vx = a.vy = 0; continue; }
    a.vx *= 0.6; a.vy *= 0.6;
    var sp = Math.sqrt(a.vx * a.vx + a.vy * a.vy); if (sp > 60) { a.vx *= 60 / sp; a.vy *= 60 / sp; }
    a.x += a.vx; a.y += a.vy;
  }
  alpha *= 0.985; ticks++;
}
function reheat(a) { alpha = Math.max(alpha, a || 0.4); ticks = Math.min(ticks, 300); if (!simOn) { simOn = true; schedule(); } }
function prelayout() {
  var t0 = performance.now(), maxMs = N > 1000 ? 2500 : 900;
  while (alpha > 0.02 && ticks < 400 && performance.now() - t0 < maxMs) tick();
  simOn = alpha > 0.02 && ticks < 400;
}

// ---------- view ----------
function resize() {
  var r = cv.getBoundingClientRect(); dpr = window.devicePixelRatio || 1;
  W = r.width; H = r.height; cv.width = Math.max(1, Math.round(W * dpr)); cv.height = Math.max(1, Math.round(H * dpr));
  schedule();
}
function bbox(list) {
  var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  list.forEach(function (n) { x0 = Math.min(x0, n.x - n.r); y0 = Math.min(y0, n.y - n.r); x1 = Math.max(x1, n.x + n.r); y1 = Math.max(y1, n.y + n.r); });
  return { x0: x0, y0: y0, x1: x1, y1: y1 };
}
function fitView(list, maxK) {
  list = list && list.length ? list : nodes.filter(visible);
  if (!list.length) { view = { k: 1, x: W / 2, y: H / 2 }; return view; }
  var b = bbox(list), pad = 70, bw = Math.max(b.x1 - b.x0, 40), bh = Math.max(b.y1 - b.y0, 40);
  var k = Math.min((W - pad * 2) / bw, (H - pad * 2) / bh, maxK || 2.5);
  return { k: k, x: W / 2 - (b.x0 + b.x1) / 2 * k, y: H / 2 - (b.y0 + b.y1) / 2 * k };
}
var anim = null;
function animateTo(target) { anim = { from: { k: view.k, x: view.x, y: view.y }, to: target, t: 0 }; schedule(); }
function toWorld(px, py) { return { x: (px - view.x) / view.k, y: (py - view.y) / view.k }; }

// ---------- drawing ----------
var raf = 0;
function schedule() { if (!raf) raf = requestAnimationFrame(frame); }
function frame() {
  raf = 0;
  if (simOn) { for (var s = 0; s < (N > 1000 ? 2 : 3) && simOn; s++) { tick(); if (alpha <= 0.02 || ticks >= 400) simOn = false; } }
  if (anim) {
    anim.t += 0.08; var u = Math.min(anim.t, 1), q = u * u * (3 - 2 * u);
    view = { k: anim.from.k + (anim.to.k - anim.from.k) * q, x: anim.from.x + (anim.to.x - anim.from.x) * q, y: anim.from.y + (anim.to.y - anim.from.y) * q };
    if (u >= 1) anim = null;
  }
  draw();
  if (simOn || anim) schedule();
}
function focusSet() {
  var f = null;
  if (matches) { f = {}; matches.forEach(function (n) { f[n.id] = 1; }); }
  else if (selected || hover) {
    var c = selected || hover; f = {}; f[c.id] = 1;
    adj[c.id].out.forEach(function (e) { f[e.t.id] = 1; }); adj[c.id].inn.forEach(function (e) { f[e.s.id] = 1; });
  }
  return f;
}
function arrow(x, y, ang, size) {
  ctx.beginPath(); ctx.moveTo(x, y);
  ctx.lineTo(x - size * Math.cos(ang - 0.4), y - size * Math.sin(ang - 0.4));
  ctx.lineTo(x - size * Math.cos(ang + 0.4), y - size * Math.sin(ang + 0.4)); ctx.closePath(); ctx.fill();
}
function draw() {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  var k = view.k, f = focusSet(), cur = selected || hover, i, e, n;
  ctx.lineCap = 'round';
  // edges
  var showEdgeLabels = k >= 2.2;
  var labelEdges = [];
  for (i = 0; i < edges.length; i++) {
    e = edges[i]; if (!visible(e.s) || !visible(e.t)) continue;
    var x1 = e.s.x * k + view.x, y1 = e.s.y * k + view.y, x2 = e.t.x * k + view.x, y2 = e.t.y * k + view.y;
    if ((x1 < -50 && x2 < -50) || (x1 > W + 50 && x2 > W + 50) || (y1 < -50 && y2 < -50) || (y1 > H + 50 && y2 > H + 50)) continue;
    var on = f ? (f[e.s.id] && f[e.t.id] && (!matches || cur)) : true;
    if (matches) on = f[e.s.id] && f[e.t.id];
    var touch = cur && (e.s === cur || e.t === cur);
    var st = e.d.status;
    ctx.globalAlpha = (f && !on && !touch) ? 0.07 : (e.s.ghost || e.t.ghost || st === 'removed') ? 0.55 : touch ? 1 : 0.65;
    var colr = st === 'added' ? COL.added : st === 'removed' ? COL.removed : st === 'changed' ? COL.changed : touch ? COL['edge-strong'] : COL.edge;
    ctx.strokeStyle = colr; ctx.fillStyle = colr;
    ctx.lineWidth = (touch ? 2 : 1.2) * (k < 0.5 ? 0.8 : 1);
    var dash = e.d.confidence === 'none' || e.d.confidence === 'low' ? [3, 3] : (DASH[e.d.type] || []);
    ctx.setLineDash(st === 'removed' ? [4, 4] : dash);
    var dx = x2 - x1, dy = y2 - y1, len = Math.sqrt(dx * dx + dy * dy) || 1, ux = dx / len, uy = dy / len;
    var rt = e.t.r * k, rs = e.s.r * k;
    if (len < rs + rt + 2) continue;
    var sx = x1 + ux * rs, sy = y1 + uy * rs, tx = x2 - ux * (rt + 1), ty = y2 - uy * (rt + 1);
    ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(tx, ty); ctx.stroke();
    ctx.setLineDash([]);
    if (len > 24) arrow(tx, ty, Math.atan2(dy, dx), Math.max(5, 7 * Math.min(k, 1.5)));
    if ((showEdgeLabels || touch) && len > 70 && (!f || on || touch)) labelEdges.push([e, (sx + tx) / 2, (sy + ty) / 2, Math.atan2(dy, dx)]);
  }
  ctx.globalAlpha = 1; ctx.setLineDash([]);
  // nodes
  for (i = 0; i < N; i++) {
    n = nodes[i]; if (!visible(n)) continue;
    var px = n.x * k + view.x, py = n.y * k + view.y, rr = n.r * Math.max(k, 0.35) ;
    if (px < -40 || px > W + 40 || py < -40 || py > H + 40) continue;
    var dim = f && !f[n.id];
    ctx.globalAlpha = dim ? 0.12 : n.ghost ? 0.5 : 1;
    ctx.beginPath(); ctx.arc(px, py, rr, 0, 6.2832);
    ctx.fillStyle = COL[n.d.type] || COL.edge;
    var lowc = n.d.confidence === 'none' || n.d.confidence === 'low';
    if (lowc || n.ghost) { ctx.globalAlpha *= 0.45; ctx.fill(); ctx.globalAlpha = dim ? 0.12 : n.ghost ? 0.5 : 1; ctx.lineWidth = 2; ctx.strokeStyle = COL[n.d.type] || COL.edge; ctx.setLineDash([4, 3]); ctx.stroke(); ctx.setLineDash([]); }
    else { ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = COL.halo; ctx.stroke(); }
    var s = n.d.status;
    if (s) { ctx.beginPath(); ctx.arc(px, py, rr + 4, 0, 6.2832); ctx.lineWidth = 3; ctx.strokeStyle = COL[s]; if (s === 'removed') ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]); }
    if (n === selected) { ctx.beginPath(); ctx.arc(px, py, rr + (s ? 8 : 5), 0, 6.2832); ctx.lineWidth = 2; ctx.strokeStyle = COL.accent; ctx.stroke(); }
    if (n.d.bug || n.d.quirk) { ctx.beginPath(); ctx.arc(px + rr * 0.75, py - rr * 0.75, Math.max(3, rr * 0.3), 0, 6.2832); ctx.fillStyle = n.d.bug ? COL.bug : COL.quirk; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = COL.halo; ctx.stroke(); }
  }
  ctx.globalAlpha = 1;
  // labels (greedy, collision-avoiding, capped)
  ctx.font = '12px system-ui, sans-serif'; ctx.textBaseline = 'middle'; ctx.textAlign = 'left'; ctx.lineJoin = 'round';
  var boxes = [], placed = 0, cap = 220, topN = N > 60 ? 18 : N;
  function tryLabel(n, force) {
    var px = n.x * k + view.x, py = n.y * k + view.y, rr = n.r * Math.max(k, 0.35);
    if (px < -20 || px > W + 20 || py < -20 || py > H + 20) return;
    var txt = n.d.name; if (txt.length > 30) txt = txt.slice(0, 29) + '\u2026';
    var w = ctx.measureText(txt).width, bx = px + rr + 4, by = py - 8, bw = w, bh = 16;
    if (!force) { for (var j = 0; j < boxes.length; j++) { var o = boxes[j]; if (bx < o[0] + o[2] && bx + bw > o[0] && by < o[1] + o[3] && by + bh > o[1]) return; } }
    boxes.push([bx, by, bw, bh]); placed++;
    ctx.globalAlpha = (f && !f[n.id]) ? 0.25 : 1;
    ctx.lineWidth = 3.5; ctx.strokeStyle = COL.halo; ctx.strokeText(txt, bx, py); ctx.fillStyle = COL.text; ctx.fillText(txt, bx, py);
    ctx.globalAlpha = 1;
  }
  var forced = [];
  if (selected) forced.push(selected); if (hover && hover !== selected) forced.push(hover);
  forced.forEach(function (n) { tryLabel(n, true); });
  if (f && (matches || cur)) { byDeg.forEach(function (n) { if (f[n.id] && visible(n) && n !== selected && n !== hover && placed < cap) tryLabel(n, false); }); }
  for (i = 0; i < N && placed < cap; i++) {
    n = byDeg[i]; if (!visible(n) || n === selected || n === hover) continue;
    if (f && !f[n.id]) continue;
    if (f && (matches || cur)) continue;
    var rr2 = n.r * k;
    if (i < topN || rr2 >= 7 || k >= 1.1) tryLabel(n, false);
  }
  // edge labels
  ctx.font = '10.5px system-ui, sans-serif'; ctx.textAlign = 'center';
  labelEdges.slice(0, 400).forEach(function (L) {
    var ang = L[3]; if (ang > Math.PI / 2 || ang < -Math.PI / 2) ang += Math.PI;
    ctx.save(); ctx.translate(L[1], L[2]); ctx.rotate(ang);
    ctx.lineWidth = 3; ctx.strokeStyle = COL.halo; ctx.strokeText(L[0].d.type, 0, -6); ctx.fillStyle = COL.muted; ctx.fillText(L[0].d.type, 0, -6); ctx.restore();
  });
  ctx.textAlign = 'left';
}

// ---------- interaction ----------
function hit(px, py) {
  var k = view.k;
  for (var i = N - 1; i >= 0; i--) {
    var n = nodes[i]; if (!visible(n)) continue;
    var dx = n.x * k + view.x - px, dy = n.y * k + view.y - py, rr = n.r * Math.max(k, 0.35) + 3;
    if (dx * dx + dy * dy <= rr * rr) return n;
  }
  return null;
}
var drag = null;
cv.addEventListener('pointerdown', function (ev) {
  var r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top, n = hit(px, py);
  cv.setPointerCapture(ev.pointerId); anim = null;
  drag = { n: n, sx: px, sy: py, moved: false, vx: view.x, vy: view.y };
  if (n) { n.pin = true; }
  cv.classList.add('drag');
});
cv.addEventListener('pointermove', function (ev) {
  var r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
  if (drag) {
    if (Math.abs(px - drag.sx) + Math.abs(py - drag.sy) > 3) drag.moved = true;
    if (drag.n && drag.moved) { var w = toWorld(px, py); drag.n.x = w.x; drag.n.y = w.y; reheat(0.15); }
    else if (!drag.n) { view.x = drag.vx + px - drag.sx; view.y = drag.vy + py - drag.sy; }
    schedule(); return;
  }
  var h = hit(px, py); if (h !== hover) { hover = h; cv.style.cursor = h ? 'pointer' : 'grab'; schedule(); }
});
function endDrag(ev) {
  if (!drag) return; var d = drag; drag = null; cv.classList.remove('drag');
  if (d.n) d.n.pin = false;
  if (!d.moved) { if (d.n) select(d.n); else select(null); }
  schedule();
}
cv.addEventListener('pointerup', endDrag); cv.addEventListener('pointercancel', endDrag);
cv.addEventListener('pointerleave', function () { if (!drag && hover) { hover = null; schedule(); } });
cv.addEventListener('wheel', function (ev) {
  ev.preventDefault(); anim = null;
  var r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
  var nk = Math.min(8, Math.max(0.05, view.k * Math.exp(-ev.deltaY * 0.0015)));
  var w = toWorld(px, py); view.k = nk; view.x = px - w.x * nk; view.y = py - w.y * nk; schedule();
}, { passive: false });
window.addEventListener('resize', resize);

// ---------- legend ----------
function buildLegend() {
  var box = $('lgitems'), counts = {}; box.textContent = '';
  nodes.forEach(function (n) { counts[n.d.type] = (counts[n.d.type] || 0) + 1; });
  TYPES.forEach(function (t) {
    if (!counts[t]) return;
    var row = el('div', 'lg'); row.title = 'Toggle ' + t;
    var dot = el('span', 'dot'); dot.style.background = 'var(--c-' + t + ')';
    row.appendChild(dot); row.appendChild(el('span', '', t.replace(/_/g, ' '))); row.appendChild(el('span', 'n', String(counts[t])));
    row.addEventListener('click', function () { hidden[t] = !hidden[t]; row.classList.toggle('off', !!hidden[t]); if (selected && hidden[selected.d.type]) select(null); schedule(); });
    box.appendChild(row);
  });
  var eb = $('eitems'), seen = {}; eb.textContent = '';
  edges.forEach(function (e) { seen[e.d.type] = 1; });
  Object.keys(DASH).forEach(function (t) {
    if (!seen[t]) return;
    var row = el('div', 'er'), c = document.createElement('canvas'); c.width = 40; c.height = 10; c.style.cssText = 'width:40px;height:10px;position:static;cursor:default';
    c.dataset.t = t; row.appendChild(c); row.appendChild(el('span', '', t)); eb.appendChild(row);
  });
  $('elegend').style.display = Object.keys(seen).length ? '' : 'none';
  if (DATA.diff) $('dlegend').style.display = 'block';
}
function paintEdgeSwatches() {
  Array.prototype.forEach.call(document.querySelectorAll('#eitems canvas'), function (c) {
    var g = c.getContext('2d'); g.clearRect(0, 0, 40, 10); g.strokeStyle = COL['edge-strong']; g.fillStyle = COL['edge-strong']; g.lineWidth = 1.5;
    g.setLineDash(DASH[c.dataset.t] || []); g.beginPath(); g.moveTo(1, 5); g.lineTo(32, 5); g.stroke(); g.setLineDash([]);
    g.beginPath(); g.moveTo(39, 5); g.lineTo(32, 2); g.lineTo(32, 8); g.closePath(); g.fill();
  });
}

// ---------- side panel ----------
function select(n) {
  selected = n; var p = $('panel');
  if (!n) { p.style.display = 'none'; p.textContent = ''; resize(); return; }
  var d = n.d; p.textContent = ''; p.style.display = 'block';
  var x = el('button', 'close', 'Close'); x.type = 'button'; x.addEventListener('click', function () { select(null); }); p.appendChild(x);
  p.appendChild(el('h2', '', d.name)); p.appendChild(el('div', 'id', d.id));
  var chips = el('div'); chips.style.marginTop = '8px';
  chips.appendChild(el('span', 'chip', d.type.replace(/_/g, ' ')));
  chips.appendChild(el('span', 'chip', 'confidence: ' + d.confidence));
  if (d.bug) chips.appendChild(el('span', 'chip bug', 'suspected bug'));
  if (d.quirk) chips.appendChild(el('span', 'chip quirk', 'quirk'));
  if (d.status) chips.appendChild(el('span', 'chip ' + d.status, d.status + (d.status === 'removed' ? ' (not in current graph)' : '')));
  p.appendChild(chips);
  if (d.module || d.symbol) p.appendChild(el('div', 'muted', [d.module, d.symbol].filter(Boolean).join('  \u00b7  ')));
  p.appendChild(el('h3', '', 'Responsibility'));
  p.appendChild(el('div', d.responsibility ? '' : 'muted', d.responsibility || 'Not described.'));
  p.appendChild(el('h3', '', 'Evidence (' + d.evidence.length + ')'));
  if (!d.evidence.length) p.appendChild(el('div', 'muted', 'No evidence recorded.'));
  d.evidence.forEach(function (ev) {
    var b = el('div', 'ev'), loc = ev.file + (ev.ls ? ':' + ev.ls + (ev.le && ev.le !== ev.ls ? '-' + ev.le : '') : '') + (ev.symbol ? '  (' + ev.symbol + ')' : '');
    b.appendChild(el('code', '', loc)); if (ev.quote) b.appendChild(el('pre', '', ev.quote)); p.appendChild(b);
  });
  function nbList(title, list, other) {
    p.appendChild(el('h3', '', title + ' (' + list.length + ')'));
    if (!list.length) p.appendChild(el('div', 'muted', 'None.'));
    list.forEach(function (e) {
      var o = other(e), b = el('button', 'nb'); b.type = 'button';
      b.appendChild(el('span', 'et', e.d.type)); b.appendChild(document.createTextNode(o.d.name + '  '));
      b.appendChild(el('span', 'et', o.id));
      b.addEventListener('click', function () { select(o); animateTo(fitView([o].concat(adj[o.id].out.map(function (z) { return z.t; }), adj[o.id].inn.map(function (z) { return z.s; })), 2)); });
      p.appendChild(b);
    });
  }
  nbList('Outgoing', adj[n.id].out, function (e) { return e.t; });
  nbList('Incoming', adj[n.id].inn, function (e) { return e.s; });
  resize();
}

// ---------- search ----------
function runSearch(zoom) {
  var q = $('q').value.trim().toLowerCase();
  if (!q) { matches = null; schedule(); return; }
  matches = nodes.filter(function (n) { return visible(n) && (n.id.toLowerCase().indexOf(q) >= 0 || n.d.name.toLowerCase().indexOf(q) >= 0); });
  if (zoom && matches.length) animateTo(fitView(matches, 2.2));
  schedule();
}
$('q').addEventListener('input', function () { runSearch(false); });
$('q').addEventListener('keydown', function (ev) { if (ev.key === 'Enter') runSearch(true); if (ev.key === 'Escape') { this.value = ''; runSearch(false); } });
$('fit').addEventListener('click', function () { animateTo(fitView()); });

// ---------- theme ----------
function applyTheme(t) {
  root.setAttribute('data-theme', t); $('theme').textContent = t === 'dark' ? 'Light' : 'Dark';
  readColors(); paintEdgeSwatches(); schedule();
}
$('theme').addEventListener('click', function () {
  var t = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('lavish-theme', t); } catch (e) {}
  applyTheme(t);
});

// ---------- boot ----------
(function boot() {
  var s = $('stats'), txt = N + ' nodes, ' + edges.length + ' edges';
  if (DATA.diff) {
    var c = { added: 0, changed: 0, removed: 0 }; nodes.forEach(function (n) { if (n.d.status) c[n.d.status]++; });
    txt += '  \u00b7  +' + c.added + ' added, ~' + c.changed + ' changed, -' + c.removed + ' removed';
  }
  s.textContent = txt;
  buildLegend(); applyTheme(root.getAttribute('data-theme') || 'light');
  resize();
  if (!N) { $('empty').style.display = 'block'; $('legend').style.display = 'none'; $('hint').style.display = 'none'; view = { k: 1, x: W / 2, y: H / 2 }; schedule(); return; }
  prelayout(); view = fitView(); schedule();
})();
window.__graphReady = true;
})();
</script>
</body>
</html>
"""
