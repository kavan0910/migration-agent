(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- mobile menu ---- */
  var menu = document.getElementById("menu");
  var panel = document.getElementById("panel");
  function setMenu(open) {
    menu.setAttribute("aria-expanded", String(open));
    menu.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    panel.setAttribute("data-open", String(open));
    panel.setAttribute("aria-hidden", String(!open));
    document.body.style.overflow = open ? "hidden" : "";
  }
  menu.addEventListener("click", function () { setMenu(menu.getAttribute("aria-expanded") !== "true"); });
  panel.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });

  /* ---- reveal on scroll, once ---- */
  var revealEls = document.querySelectorAll(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    revealEls.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -48px 0px" });
    revealEls.forEach(function (el) { io.observe(el); });
  }

  /* ---- tagline: words activate one at a time as they cross the trigger line ---- */
  var tag = document.getElementById("tagline");
  var words = tag.textContent.trim().split(/\s+/);
  tag.textContent = "";
  var spans = words.map(function (w, i) {
    var s = document.createElement("span");
    s.className = "w"; s.textContent = w;
    tag.appendChild(s);
    if (i < words.length - 1) tag.appendChild(document.createTextNode(" "));
    return s;
  });
  if (reduce || !("IntersectionObserver" in window)) {
    spans.forEach(function (s) { s.classList.add("on"); });
  } else {
    var wio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("on"); wio.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -35% 0px", threshold: 0 });   // trigger line at 65% of the viewport height
    spans.forEach(function (s) { wio.observe(s); });
  }

  /* ---- current section in the nav ---- */
  var links = Array.prototype.slice.call(document.querySelectorAll(".nav-links a"));
  var secs = links.map(function (a) { return document.querySelector(a.getAttribute("href")); });
  if ("IntersectionObserver" in window) {
    var nio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) { a.removeAttribute("aria-current"); });
        var i = secs.indexOf(en.target);
        if (i > -1) links[i].setAttribute("aria-current", "true");
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    secs.forEach(function (s) { if (s) nio.observe(s); });
  }

  /* ---- hero terminal: real output from a run on the Express sample ---- */
  var term = document.getElementById("term");
  var script = [
    { cmd: "migration-agent ingest my-service.migration", out: ["ingested 18 files, skipped 1, secret findings 2"] },
    { cmd: "migration-agent extract my-service.migration", out: ["inventory: 29 items {'config_key': 4, 'dependency': 3, 'route': 8, 'source_file': 14}"] },
    { cmd: "migration-agent analyze my-service.migration --max-usd 1", out: ["plan: 1 chunk(s), estimated cost $0.01", "CH-0001: 15 nodes", "done: {'chunks': 1, 'nodes': 39, 'edges': 30, 'coverage_complete': True, …}"] }
  ];
  function line(html, cls) {
    var d = document.createElement("div");
    d.className = "l " + (cls || "");
    d.innerHTML = html;
    term.appendChild(d);
    return d;
  }
  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
  function renderOut(t) { return esc(t).replace(/(\d+ files|secret findings \d+|\$0\.01|True)/g, '<span class="ok">$1</span>'); }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  async function play() {
    for (var s of script) {
      var d = line('<span class="p">$</span> <span class="t"></span><span class="caret"></span>');
      var t = d.querySelector(".t");
      for (var i = 1; i <= s.cmd.length; i++) { t.textContent = s.cmd.slice(0, i); await wait(22); }
      d.querySelector(".caret").remove();
      await wait(260);
      for (var o of s.out) { line(renderOut(o)); await wait(340); }
      await wait(260);
    }
    var last = line('<span class="p">$</span> <span class="caret"></span>');
  }
  function showStatic() {
    script.forEach(function (s) {
      line('<span class="p">$</span> ' + esc(s.cmd));
      s.out.forEach(function (o) { line(renderOut(o)); });
    });
  }
  if (reduce) { showStatic(); } else {
    var started = false;
    var tio = new IntersectionObserver(function (en) {
      if (en[0].isIntersecting && !started) { started = true; tio.disconnect(); setTimeout(play, 600); }
    }, { threshold: 0.3 });
    tio.observe(term);
  }

  /* ---- copy quickstart commands ---- */
  var copyBtn = document.getElementById("copy");
  var msg = document.getElementById("copymsg");
  copyBtn.addEventListener("click", async function () {
    var text = document.getElementById("cmds").innerText;
    var label = copyBtn.querySelector("span");
    var icon = copyBtn.querySelector("i");
    try {
      await navigator.clipboard.writeText(text);
      copyBtn.dataset.state = "done"; label.textContent = "Copied"; icon.className = "ph ph-check";
      msg.textContent = "Commands copied. Replace the placeholders before you run them.";
    } catch (e) {
      copyBtn.dataset.state = "error"; label.textContent = "Copy failed"; icon.className = "ph ph-warning";
      msg.textContent = "Copy failed. Select the commands and copy them by hand.";
    }
    setTimeout(function () {
      copyBtn.dataset.state = "idle"; label.textContent = "Copy"; icon.className = "ph ph-copy"; msg.textContent = "";
    }, 3000);
  });
})();
