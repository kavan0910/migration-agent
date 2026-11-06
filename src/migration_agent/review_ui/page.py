"""The single self-contained review page (no external network)."""
from __future__ import annotations

import re


def render_page(base: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_-]+", base):
        raise ValueError("bad base path")
    return _TEMPLATE.replace("__BASE__", base)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Migration review</title>
<script>
try { var t = localStorage.getItem('lavish-theme'); document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light'); } catch (e) { document.documentElement.setAttribute('data-theme', 'light'); }
</script>
<style>
:root {
  --bg: #f7f8fa; --panel: #ffffff; --text: #1c2430; --muted: #5d6879; --border: #d9dee6;
  --accent: #2457d6; --added: #138a43; --changed: #c77700; --removed: #cc2f3a;
}
:root[data-theme="dark"] {
  --bg: #12161d; --panel: #1b212b; --text: #e4e9f1; --muted: #9aa5b8; --border: #2d3644;
  --accent: #7ba3ff; --added: #4cd081; --changed: #f0b04a; --removed: #ff6b75;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { background: var(--bg); color: var(--text); font: 13px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; display: flex; flex-direction: column; overflow: hidden; }
header { display: flex; gap: 12px; align-items: center; padding: 8px 14px; background: var(--panel); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
header h1 { font-size: 15px; margin: 0; font-weight: 650; }
header .sp { flex: 1; }
#stats { color: var(--muted); font-size: 12px; }
button, select, input, textarea { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 5px 10px; font: inherit; }
button { cursor: pointer; }
button:hover { border-color: var(--accent); }
button:focus-visible, .iid:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
textarea { width: 100%; min-height: 60px; resize: vertical; }
#layout { flex: 1; display: flex; min-height: 0; }
nav { width: 240px; flex: none; background: var(--panel); border-right: 1px solid var(--border); overflow-y: auto; padding: 8px 0; }
nav h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 10px 14px 4px; font-weight: 600; }
nav a { display: block; padding: 4px 14px; color: var(--text); text-decoration: none; cursor: pointer; border-left: 3px solid transparent; }
nav a:hover { background: var(--bg); }
nav a.on { border-left-color: var(--accent); background: var(--bg); font-weight: 600; }
nav .badge { float: right; color: var(--muted); font-size: 11px; }
main { flex: 1; overflow-y: auto; padding: 18px 26px; min-width: 0; }
aside { width: 330px; flex: none; background: var(--panel); border-left: 1px solid var(--border); overflow-y: auto; padding: 12px 14px; }
aside h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 0 0 6px; font-weight: 600; }
main h2.title { font-size: 20px; margin: 0 0 10px; }
main h3, main h4, main h5 { margin: 14px 0 6px; }
main ul { padding-left: 20px; }
main code, main pre { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; font-size: 12px; }
main pre { padding: 8px; overflow-x: auto; }
.iid { color: var(--accent); cursor: pointer; border-bottom: 1px dotted var(--accent); font-variant-numeric: tabular-nums; }
.iid.has { background: color-mix(in srgb, var(--changed) 25%, transparent); }
.card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px; margin: 0 0 10px; }
.card .t { font-weight: 600; }
.muted { color: var(--muted); font-size: 12px; }
.sev-high { color: var(--removed); } .sev-medium { color: var(--changed); } .sev-low { color: var(--added); }
.pill { display: inline-block; border: 1px solid var(--border); border-radius: 10px; padding: 0 7px; font-size: 11px; color: var(--muted); margin-right: 4px; }
blockquote { margin: 6px 0; padding: 2px 8px; border-left: 3px solid var(--border); color: var(--muted); }
.row { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.stat { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.stat b { display: block; font-size: 20px; }
.added { color: var(--added); } .removed { color: var(--removed); } .changed { color: var(--changed); }
iframe { width: 100%; height: 70vh; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); }
#toast { position: fixed; bottom: 14px; left: 50%; transform: translateX(-50%); background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px; display: none; }
@media (max-width: 900px) { aside { display: none; } nav { width: 170px; } }
</style>
</head>
<body>
<header>
  <h1 id="pname">Migration review</h1>
  <span id="stats"></span>
  <span class="sp"></span>
  <button id="theme" type="button">Theme</button>
</header>
<div id="layout">
  <nav id="nav" aria-label="Sections"></nav>
  <main id="main" tabindex="-1"></main>
  <aside id="side" aria-label="Comments"></aside>
</div>
<div id="toast" role="status"></div>
<script>
(function () {
  "use strict";
  var BASE = "__BASE__";
  var S = null, view = "sec-0", selected = null;

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) { if (k === "text") e.textContent = attrs[k]; else if (k === "cls") e.className = attrs[k]; else e.setAttribute(k, attrs[k]); }
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function toast(msg) { var t = document.getElementById("toast"); t.textContent = msg; t.style.display = "block"; setTimeout(function () { t.style.display = "none"; }, 2500); }
  function api(path, opts) {
    return fetch(BASE + path, opts).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; }); });
  }
  function post(path, body) { return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  function load() { return api("/api/state").then(function (s) { S = s; renderAll(); }); }

  function latestRes() {
    var m = {}; S.resolutions.resolutions.forEach(function (r) { m[r.finding_id] = r; }); return m;
  }
  function commentsFor(id) { return S.comments.comments.filter(function (c) { return c.item_id === id; }); }
  function idLink(id) {
    var s = el("span", { cls: "iid", tabindex: "0", role: "button", "data-id": id, text: id });
    return s;
  }

  function renderNav() {
    var nav = document.getElementById("nav"); nav.textContent = "";
    function add(id, label, badge) {
      var a = el("a", { text: label, tabindex: "0", cls: view === id ? "on" : "" });
      if (badge != null) a.appendChild(el("span", { cls: "badge", text: String(badge) }));
      a.addEventListener("click", function () { view = id; renderAll(); });
      a.addEventListener("keydown", function (e) { if (e.key === "Enter") a.click(); });
      nav.appendChild(a);
    }
    nav.appendChild(el("h2", { text: "Review" }));
    var res = latestRes();
    var open = S.findings.findings.filter(function (f) { return !res[f.id] || res[f.id].status === "open"; }).length;
    add("findings", "Findings", open + "/" + S.findings.findings.length);
    add("coverage", "Coverage");
    add("versions", "Versions & diff", S.versions.length);
    add("graph", "Graph");
    nav.appendChild(el("h2", { text: "Understanding" }));
    S.sections.forEach(function (s) { add(s.id, s.title); });
  }

  function renderSide() {
    var side = document.getElementById("side"); side.textContent = "";
    side.appendChild(el("h2", { text: "Comments" }));
    if (!selected) { side.appendChild(el("p", { cls: "muted", text: "Click an item ID to comment on it." })); return; }
    side.appendChild(el("div", { cls: "card" }, [idLink(selected)]));
    commentsFor(selected).forEach(function (c) {
      side.appendChild(el("div", { cls: "card" }, [
        el("div", { cls: "muted", text: c.id + " · " + c.author + " · " + c.created + (c.processed ? " · processed" : "") }),
        el("div", { text: c.text })]));
    });
    var ta = el("textarea", { "aria-label": "Comment", maxlength: "5000", placeholder: "Add a comment" });
    var btn = el("button", { type: "button", text: "Post comment" });
    btn.addEventListener("click", function () {
      var text = ta.value.trim(); if (!text) return;
      post("/api/comments", { item_id: selected, text: text }).then(function () { toast("Comment saved"); return load(); })
        .catch(function (e) { toast("Error: " + e.message); });
    });
    side.appendChild(ta); side.appendChild(el("div", { cls: "row" }, [btn]));
  }

  function renderDoc(sec) {
    var main = document.getElementById("main"); main.textContent = "";
    main.appendChild(el("h2", { cls: "title", text: sec.title }));
    var box = el("div"); box.innerHTML = sec.html;   // server-escaped, whitelisted markup only
    main.appendChild(box);
    var ids = {}; S.comments.comments.forEach(function (c) { ids[c.item_id] = 1; });
    box.querySelectorAll(".iid").forEach(function (n) { if (ids[n.getAttribute("data-id")]) n.classList.add("has"); });
  }

  function renderFindings() {
    var main = document.getElementById("main"); main.textContent = "";
    main.appendChild(el("h2", { cls: "title", text: "Findings" }));
    var ff = S.findings;
    main.appendChild(el("p", { cls: "muted", text: ff.findings.length + " findings · graph v" + ff.graph_version + " · " + ff.claims_extracted + " claims checked · " + ff.dropped_unverifiable + " dropped as unverifiable" }));
    var res = latestRes();
    if (!ff.findings.length) main.appendChild(el("p", { text: "No findings." }));
    ff.findings.forEach(function (f) {
      var cur = res[f.id];
      var card = el("div", { cls: "card" });
      card.appendChild(el("div", {}, [el("span", { cls: "t", text: f.id + "  " + f.title }), " ",
        el("span", { cls: "pill sev-" + f.severity, text: f.severity }), el("span", { cls: "pill", text: f.kind }),
        el("span", { cls: "pill", text: cur ? cur.status : "open" })]));
      if (f.detail) card.appendChild(el("div", { text: f.detail }));
      if (f.area) card.appendChild(el("div", { cls: "muted", text: "area: " + f.area }));
      if (f.item_ids.length) { var d = el("div", { cls: "muted" }, ["items: "]); f.item_ids.forEach(function (i) { d.appendChild(idLink(i)); d.appendChild(document.createTextNode(" ")); }); card.appendChild(d); }
      f.quotes.forEach(function (q) { card.appendChild(el("blockquote", { text: q.text }, [el("div", { cls: "muted", text: q.source + (q.verified ? " (verified)" : " (unverified)") })])); });
      if (cur && cur.note) card.appendChild(el("div", { cls: "muted", text: "note: " + cur.note }));
      var sel = el("select", { "aria-label": "Status for " + f.id });
      ["open", "accepted_doc", "accepted_code", "dismissed", "needs_reanalysis", "resolved"].forEach(function (s) {
        var o = el("option", { value: s, text: s }); if ((cur ? cur.status : "open") === s) o.selected = true; sel.appendChild(o);
      });
      var note = el("input", { type: "text", placeholder: "note", maxlength: "5000", "aria-label": "Note for " + f.id });
      var b = el("button", { type: "button", text: "Save" });
      b.addEventListener("click", function () {
        post("/api/resolutions", { finding_id: f.id, status: sel.value, note: note.value }).then(function () { toast("Resolution saved"); return load(); })
          .catch(function (e) { toast("Error: " + e.message); });
      });
      card.appendChild(el("div", { cls: "row" }, [sel, note, b]));
      main.appendChild(card);
    });
  }

  function renderCoverage() {
    var main = document.getElementById("main"); main.textContent = "";
    main.appendChild(el("h2", { cls: "title", text: "Coverage" }));
    var c = S.coverage;
    function grid(title, obj) {
      main.appendChild(el("h3", { text: title }));
      var g = el("div", { cls: "grid" });
      Object.keys(obj).sort().forEach(function (k) { g.appendChild(el("div", { cls: "stat" }, [el("b", { text: String(obj[k]) }), k])); });
      if (!Object.keys(obj).length) g.appendChild(el("span", { cls: "muted", text: "none" }));
      main.appendChild(g);
    }
    grid("Graph", { nodes: c.node_total, edges: c.edge_total });
    grid("Nodes by type", c.nodes_by_type);
    grid("Inventory", c.inventory);
    main.appendChild(el("h3", { text: "From the document's coverage matrix" }));
    var ul = el("ul"); c.doc.forEach(function (r) { ul.appendChild(el("li", { text: r[0] + ": " + r[1] })); });
    if (!c.doc.length) ul.appendChild(el("li", { cls: "muted", text: "none" }));
    main.appendChild(ul);
  }

  function renderVersions() {
    var main = document.getElementById("main"); main.textContent = "";
    main.appendChild(el("h2", { cls: "title", text: "Versions & diff" }));
    if (S.versions.length < 1) { main.appendChild(el("p", { text: "No graph snapshots." })); return; }
    var a = el("select", { "aria-label": "From version" }), b = el("select", { "aria-label": "To version" });
    S.versions.forEach(function (v, i) {
      var oa = el("option", { value: String(v), text: "v" + v }), ob = el("option", { value: String(v), text: "v" + v });
      if (i === Math.max(0, S.versions.length - 2)) oa.selected = true; if (i === S.versions.length - 1) ob.selected = true;
      a.appendChild(oa); b.appendChild(ob);
    });
    var go = el("button", { type: "button", text: "Diff" });
    var out = el("div");
    go.addEventListener("click", function () {
      api("/api/diff?a=" + encodeURIComponent(a.value) + "&b=" + encodeURIComponent(b.value)).then(function (d) {
        out.textContent = "";
        out.appendChild(el("p", { cls: "muted", text: "v" + d.a + " -> v" + d.b + " · edges: +" + d.added_edges + " -" + d.removed_edges + " ~" + d.changed_edges }));
        [["added_nodes", "Added", "added"], ["removed_nodes", "Removed", "removed"], ["changed_nodes", "Changed", "changed"]].forEach(function (g) {
          out.appendChild(el("h3", { cls: g[2], text: g[1] + " nodes (" + d[g[0]].length + ")" }));
          var ul = el("ul"); d[g[0]].forEach(function (n) { var li = el("li"); li.appendChild(idLink(n.id)); li.appendChild(document.createTextNode(" " + n.name)); ul.appendChild(li); });
          out.appendChild(ul);
        });
        var fr = el("iframe", { title: "Diff graph", sandbox: "allow-scripts allow-same-origin", src: BASE + "/graph?v=" + encodeURIComponent(d.b) + "&prev=" + encodeURIComponent(d.a) });
        out.appendChild(fr);
      }).catch(function (e) { toast("Error: " + e.message); });
    });
    main.appendChild(el("div", { cls: "row" }, [a, "->", b, go]));
    main.appendChild(out);
  }

  function renderGraph() {
    var main = document.getElementById("main"); main.textContent = "";
    main.appendChild(el("h2", { cls: "title", text: "Graph" }));
    main.appendChild(el("iframe", { title: "Relationship graph", sandbox: "allow-scripts allow-same-origin", src: BASE + "/graph" }));
  }

  function renderAll() {
    document.getElementById("pname").textContent = (S.project || "Project") + " review";
    document.getElementById("stats").textContent = S.versions.length + " versions · " + S.comments.comments.length + " comments";
    renderNav(); renderSide();
    if (view === "findings") renderFindings();
    else if (view === "coverage") renderCoverage();
    else if (view === "versions") renderVersions();
    else if (view === "graph") renderGraph();
    else { var sec = S.sections.filter(function (s) { return s.id === view; })[0] || S.sections[0]; if (sec) renderDoc(sec); else document.getElementById("main").textContent = "No understanding document yet."; }
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest(".iid") : null;
    if (t) { selected = t.getAttribute("data-id"); renderSide(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.classList && e.target.classList.contains("iid")) e.target.click();
  });
  document.getElementById("theme").addEventListener("click", function () {
    var r = document.documentElement, n = r.getAttribute("data-theme") === "dark" ? "light" : "dark";
    r.setAttribute("data-theme", n); try { localStorage.setItem("lavish-theme", n); } catch (e) {}
  });
  load().catch(function (e) { document.getElementById("main").textContent = "Failed to load: " + e.message; });
})();
</script>
</body>
</html>
"""
