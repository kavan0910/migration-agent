(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var hasIO = "IntersectionObserver" in window;
  var root = document.documentElement;
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  /* ---- theme ---- */
  var tbtn = $("#theme");
  function applyTheme(t, save) {
    root.setAttribute("data-theme", t);
    tbtn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
    var m = $('meta[name="theme-color"]'); if (m) m.setAttribute("content", t === "dark" ? "#000000" : "#ffffff");
    if (save) { try { localStorage.setItem("ma-theme", t); } catch (e) { /* storage blocked: choice will not persist */ } }
  }
  applyTheme(root.getAttribute("data-theme") || "dark", false);
  tbtn.addEventListener("click", function () { applyTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark", true); });

  /* ---- mobile menu ---- */
  var burger = $("#burger"), panel = $("#mpanel");
  function setMenu(open) {
    burger.setAttribute("aria-expanded", String(open)); burger.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    panel.setAttribute("data-open", String(open)); panel.setAttribute("aria-hidden", String(!open)); document.body.style.overflow = open ? "hidden" : "";
  }
  burger.addEventListener("click", function () { setMenu(burger.getAttribute("aria-expanded") !== "true"); });
  panel.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });

  /* ---- reveal once ---- */
  var rv = $$(".rv");
  if (reduce || !hasIO) { rv.forEach(function (e) { e.classList.add("in"); }); } else {
    var rio = new IntersectionObserver(function (en) { en.forEach(function (x) { if (x.isIntersecting) { x.target.classList.add("in"); rio.unobserve(x.target); } }); }, { threshold: 0.12, rootMargin: "0px 0px -48px 0px" });
    rv.forEach(function (e) { rio.observe(e); });
  }

  /* ---- statement: words light up one at a time ---- */
  var st = $("#statement"), words = st.textContent.trim().split(/\s+/); st.textContent = "";
  var spans = words.map(function (w, i) {
    var s = document.createElement("span"); s.className = "w"; s.textContent = w; st.appendChild(s);
    if (i < words.length - 1) st.appendChild(document.createTextNode(" ")); return s;
  });
  if (reduce || !hasIO) { spans.forEach(function (s) { s.classList.add("on"); }); } else {
    var wio = new IntersectionObserver(function (en) { en.forEach(function (x) { if (x.isIntersecting) { x.target.classList.add("on"); wio.unobserve(x.target); } }); }, { rootMargin: "0px 0px -35% 0px" });
    spans.forEach(function (s) { wio.observe(s); });
  }

  /* ---- knowledge graph copies ---- */
  var tpl = $("#gtpl");
  $$(".gslot").forEach(function (slot) { slot.appendChild(tpl.content.cloneNode(true)); });
  var graphs = $$(".gsvg");
  graphs.forEach(function (g) {
    if (reduce || !hasIO) { g.classList.add("on"); return; }
    var io = new IntersectionObserver(function (en) { if (en[0].isIntersecting) { g.classList.add("on"); io.disconnect(); } }, { threshold: 0.3 });
    io.observe(g);
  });

  /* ---- hero terminal (real output from a run on the Express sample) ---- */
  var term = $("#term");
  var script = [
    { cmd: "migration-agent ingest my-service.migration", out: ["ingested 18 files, skipped 1, secret findings 2"] },
    { cmd: "migration-agent extract my-service.migration", out: ["inventory: 29 items {'config_key': 4, 'dependency': 3, 'route': 8, 'source_file': 14}"] },
    { cmd: "migration-agent analyze my-service.migration --max-usd 1", out: ["plan: 1 chunk(s), estimated cost $0.01", "CH-0001: 15 nodes", "done: {'chunks': 1, 'nodes': 39, 'edges': 30, 'coverage_complete': True, …}"] }
  ];
  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
  function hi(t) { return esc(t).replace(/(\d+ files|secret findings \d+|\$0\.01|True)/g, '<span class="ok">$1</span>'); }
  function line(html) { var d = document.createElement("div"); d.className = "l"; d.innerHTML = html; term.appendChild(d); return d; }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  async function play() {
    for (var s of script) {
      var d = line('<span class="p">$</span> <span class="t"></span><span class="caret"></span>'), t = d.querySelector(".t");
      for (var i = 1; i <= s.cmd.length; i++) { t.textContent = s.cmd.slice(0, i); await wait(22); }
      d.querySelector(".caret").remove(); await wait(260);
      for (var o of s.out) { line(hi(o)); await wait(340); }
      await wait(260);
    }
    line('<span class="p">$</span> <span class="caret"></span>');
  }
  if (reduce) { script.forEach(function (s) { line('<span class="p">$</span> ' + esc(s.cmd)); s.out.forEach(function (o) { line(hi(o)); }); }); }
  else if (hasIO) { var started = false; var tio = new IntersectionObserver(function (en) { if (en[0].isIntersecting && !started) { started = true; tio.disconnect(); setTimeout(play, 700); } }, { threshold: 0.2 }); tio.observe(term); }

  /* ---- scroll linked moments, one rAF loop, transform and opacity only ---- */
  var bar = $("#progress"), device = $("#device"), hero = $("#top"), story = $(".story"), ticking = false;
  function clamp(v, a, b) { return Math.min(b, Math.max(a, v)); }
  function frame() {
    ticking = false;
    var y = window.scrollY, vh = window.innerHeight;
    var h = document.documentElement.scrollHeight - vh;
    bar.style.transform = "scaleX(" + (h > 0 ? clamp(y / h, 0, 1) : 0) + ")";
    if (!reduce && device) {                                  // the product window grows into place as you scroll
      var r = device.parentNode.getBoundingClientRect();
      var p = clamp(1 - r.top / vh, 0, 1);                    // 0 when it enters the viewport, 1 when it sits at the top
      var e = 1 - Math.pow(1 - clamp(p / 0.65, 0, 1), 3);
      device.style.transform = "translateY(" + ((1 - e) * 48) + "px) scale(" + (0.86 + 0.14 * e) + ")";
    }
    if (story && window.innerWidth > 833 && !reduce) {         // pinned story: three steps across the tall section
      var sr = story.getBoundingClientRect(), total = story.offsetHeight - vh;
      var sp = clamp(-sr.top / total, 0, 0.999);
      var step = Math.floor(sp * 3);
      if (story.getAttribute("data-step") !== String(step)) story.setAttribute("data-step", String(step));
    }
  }
  function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(frame); } }
  window.addEventListener("scroll", onScroll, { passive: true }); window.addEventListener("resize", onScroll); frame();

  /* ---- nav current section ---- */
  var links = $$(".links a"), secs = links.map(function (a) { return $(a.getAttribute("href")); });
  if (hasIO) {
    var nio = new IntersectionObserver(function (en) { en.forEach(function (x) { if (!x.isIntersecting) return; links.forEach(function (a) { a.removeAttribute("aria-current"); }); var i = secs.indexOf(x.target); if (i > -1) links[i].setAttribute("aria-current", "true"); }); }, { rootMargin: "-45% 0px -50% 0px" });
    secs.forEach(function (s) { if (s) nio.observe(s); });
  }

  /* ---- carousel ---- */
  var sc = $("#scroller"), prev = $("#prev"), next = $("#next"), slides = $$(".slide");
  function upd() {
    prev.disabled = sc.scrollLeft < 8; next.disabled = sc.scrollLeft + sc.clientWidth >= sc.scrollWidth - 8;
    var box = sc.getBoundingClientRect();
    slides.forEach(function (s) { var r = s.getBoundingClientRect(); s.classList.toggle("on", reduce || (r.left >= box.left - 8 && r.right <= box.right + 8)); });
  }
  function by(dir) { var w = slides[0].getBoundingClientRect().width + 24; sc.scrollBy({ left: dir * w, behavior: reduce ? "auto" : "smooth" }); }
  prev.addEventListener("click", function () { by(-1); }); next.addEventListener("click", function () { by(1); });
  sc.addEventListener("scroll", function () { requestAnimationFrame(upd); }, { passive: true }); window.addEventListener("resize", upd); upd();
  sc.addEventListener("keydown", function (e) { if (e.key === "ArrowRight") by(1); if (e.key === "ArrowLeft") by(-1); });

  /* ---- count up ---- */
  function countTo(el, to, dec) {
    var t0 = null, dur = 1300;
    function step(ts) { if (t0 === null) t0 = ts; var p = clamp((ts - t0) / dur, 0, 1), e = 1 - Math.pow(1 - p, 4); el.textContent = (to * e).toFixed(dec); if (p < 1) requestAnimationFrame(step); }
    el.textContent = (0).toFixed(dec); requestAnimationFrame(step);
  }
  if (!reduce && hasIO) {
    var cio = new IntersectionObserver(function (en) { en.forEach(function (x) { if (!x.isIntersecting) return; var el = x.target;
      if (el.hasAttribute("data-count")) countTo(el, parseInt(el.getAttribute("data-count"), 10), 0); else countTo(el, parseFloat(el.getAttribute("data-countf")), 4);
      cio.unobserve(el); }); }, { threshold: 0.6 });
    $$("[data-count],[data-countf]").forEach(function (c) { cio.observe(c); });
  }

  /* ---- copy commands ---- */
  var cb = $("#copy"), msg = $("#copymsg");
  cb.addEventListener("click", async function () {
    var label = cb.querySelector("span"), icon = cb.querySelector("i");
    try { await navigator.clipboard.writeText($("#cmds").innerText); cb.dataset.state = "done"; label.textContent = "Copied"; icon.className = "ph ph-check"; msg.textContent = "Commands copied. Replace the placeholders before you run them."; }
    catch (e) { cb.dataset.state = "error"; label.textContent = "Copy failed"; icon.className = "ph ph-warning"; msg.textContent = "Copy failed. Select the commands and copy them by hand."; }
    setTimeout(function () { cb.dataset.state = "idle"; label.textContent = "Copy"; icon.className = "ph ph-copy"; msg.textContent = ""; }, 3000);
  });
})();
