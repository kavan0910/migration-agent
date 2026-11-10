"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { layoutGraph, type Point } from "@/lib/graphLayout";
import type { GraphEdge, GraphNode } from "@/lib/types";
import { typeLabel } from "@/lib/format";
import { TypeGlyph } from "./ui";

interface View { x: number; y: number; k: number }
const W = 1000, H = 700;

function drawShape(ctx: CanvasRenderingContext2D, type: string, x: number, y: number, r: number) {
  ctx.beginPath();
  switch (type) {
    case "component": ctx.rect(x - r, y - r, r * 2, r * 2); break;
    case "endpoint": ctx.moveTo(x, y - r * 1.25); ctx.lineTo(x + r * 1.25, y); ctx.lineTo(x, y + r * 1.25); ctx.lineTo(x - r * 1.25, y); ctx.closePath(); break;
    case "business_rule": ctx.moveTo(x, y - r * 1.2); ctx.lineTo(x + r * 1.15, y + r * 0.9); ctx.lineTo(x - r * 1.15, y + r * 0.9); ctx.closePath(); break;
    case "external_dependency": for (let i = 0; i < 6; i++) { const a = (Math.PI / 3) * i; ctx.lineTo(x + Math.cos(a) * r * 1.15, y + Math.sin(a) * r * 1.15); } ctx.closePath(); break;
    default: ctx.arc(x, y, r, 0, Math.PI * 2);
  }
}

export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** Canvas renderer with pan/zoom. Layout is computed once per node set (capped by the caller). */
export function GraphCanvas({ nodes, edges, selectedId, onSelect }: GraphCanvasProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [hover, setHover] = useState<string | null>(null);
  const [panning, setPanning] = useState(false);
  const [themeTick, setThemeTick] = useState(0);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);

  const key = useMemo(() => nodes.map((n) => n.id).join("|") + "#" + edges.length, [nodes, edges]);
  const pos = useMemo<Map<string, Point>>(() => layoutGraph(nodes, edges, { width: W, height: H, focusId: selectedId }),
    // Recompute only when the node/edge set changes, not on every selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key]);
  const neighbours = useMemo(() => {
    const s = new Set<string>();
    if (selectedId) for (const e of edges) { if (e.source === selectedId) s.add(e.target); if (e.target === selectedId) s.add(e.source); }
    return s;
  }, [edges, selectedId]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    const mo = new MutationObserver(() => setThemeTick((t) => t + 1));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onMq = () => setThemeTick((t) => t + 1);
    mq.addEventListener("change", onMq);
    return () => { ro.disconnect(); mo.disconnect(); mq.removeEventListener("change", onMq); };
  }, []);

  // fit to view whenever the node set changes
  useEffect(() => {
    const k = Math.min(size.w / W, size.h / H) * 0.95;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setView({ k, x: (size.w - W * k) / 2, y: (size.h - H * k) / 2 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, size.w > 0]);

  const toWorld = useCallback((px: number, py: number) => ({ x: (px - view.x) / view.k, y: (py - view.y) / view.k }), [view]);
  const radius = nodes.length > 250 ? 5 : 7;

  const hit = useCallback((px: number, py: number): string | null => {
    const w = toWorld(px, py);
    let best: string | null = null, bd = (radius * 1.8) ** 2 / (view.k * view.k) + 25 / view.k;
    for (const n of nodes) {
      const p = pos.get(n.id);
      if (!p) continue;
      const d = (p.x - w.x) ** 2 + (p.y - w.y) ** 2;
      if (d < bd) { bd = d; best = n.id; }
    }
    return best;
  }, [nodes, pos, toWorld, radius, view.k]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(size.w * dpr);
    c.height = Math.round(size.h * dpr);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const cs = getComputedStyle(document.documentElement);
    const v = (n: string) => cs.getPropertyValue(n).trim() || "#888";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);
    const focus = selectedId ?? hover;
    ctx.lineWidth = 1 / view.k;
    for (const e of edges) {
      const a = pos.get(e.source), b = pos.get(e.target);
      if (!a || !b) continue;
      const hot = focus && (e.source === focus || e.target === focus);
      ctx.strokeStyle = hot ? v("--accent") : v("--line-strong");
      ctx.globalAlpha = focus ? (hot ? 1 : 0.18) : 0.55;
      ctx.lineWidth = (hot ? 2 : 1) / view.k;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    const labelAll = view.k > 1.4 || nodes.length < 40;
    ctx.font = `${12 / view.k}px ${v("--font-geist-mono") || "monospace"}, monospace`;
    ctx.textBaseline = "middle";
    for (const n of nodes) {
      const p = pos.get(n.id);
      if (!p) continue;
      const dim = focus && n.id !== focus && !neighbours.has(n.id) && !(hover === n.id);
      ctx.globalAlpha = dim ? 0.3 : 1;
      ctx.fillStyle = v(`--t-${n.type}`);
      drawShape(ctx, n.type, p.x, p.y, radius);
      ctx.fill();
      if (n.id === selectedId || n.id === hover) {
        ctx.lineWidth = 2.5 / view.k;
        ctx.strokeStyle = v("--ink");
        drawShape(ctx, n.type, p.x, p.y, radius + 3);
        ctx.stroke();
      }
      if (labelAll || n.id === selectedId || n.id === hover || neighbours.has(n.id)) {
        ctx.globalAlpha = dim ? 0.3 : 1;
        ctx.fillStyle = v("--ink");
        const label = n.name.length > 34 ? n.name.slice(0, 33) + "…" : n.name;
        ctx.fillText(label, p.x + radius + 4, p.y);
      }
    }
    ctx.restore();
  }, [nodes, edges, pos, view, size, selectedId, hover, neighbours, radius, themeTick]);

  // wheel zoom (needs a non-passive listener to preventDefault)
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const r = c.getBoundingClientRect();
      const px = ev.clientX - r.left, py = ev.clientY - r.top;
      setView((v) => {
        const k = Math.max(0.2, Math.min(8, v.k * Math.exp(-ev.deltaY * 0.0015)));
        return { k, x: px - ((px - v.x) / v.k) * k, y: py - ((py - v.y) / v.k) * k };
      });
    };
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => c.removeEventListener("wheel", onWheel);
  }, []);

  const zoom = (f: number) => setView((v) => { const k = Math.max(0.2, Math.min(8, v.k * f)); const cx = size.w / 2, cy = size.h / 2; return { k, x: cx - ((cx - v.x) / v.k) * k, y: cy - ((cy - v.y) / v.k) * k }; });
  const reset = () => { const k = Math.min(size.w / W, size.h / H) * 0.95; setView({ k, x: (size.w - W * k) / 2, y: (size.h - H * k) / 2 }); };
  const hovered = hover ? nodes.find((n) => n.id === hover) : null;
  const types = [...new Set(nodes.map((n) => n.type))].sort();

  return (
    <div ref={wrap} className="dotgrid relative h-full min-h-[420px] overflow-hidden rounded-xl border border-line bg-surface">
      <canvas
        ref={canvas} tabIndex={0} role="img"
        aria-label={`Relationship graph with ${nodes.length} nodes and ${edges.length} edges. This view is a visual aid; use the node list for full keyboard and screen reader access. Plus and minus zoom, arrow keys pan.`}
        style={{ width: size.w, height: size.h, touchAction: "none", cursor: panning ? "grabbing" : hover ? "pointer" : "grab" }}
        onPointerDown={(e) => { (e.target as Element).setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false }; }}
        onPointerMove={(e) => {
          const d = drag.current;
          const r = e.currentTarget.getBoundingClientRect();
          if (d) {
            const dx = e.clientX - d.x, dy = e.clientY - d.y;
            if (Math.abs(dx) + Math.abs(dy) > 3 && !d.moved) { d.moved = true; setPanning(true); }
            if (d.moved) setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
          } else setHover(hit(e.clientX - r.left, e.clientY - r.top));
        }}
        onPointerUp={(e) => {
          const d = drag.current; drag.current = null; setPanning(false);
          if (d && !d.moved) { const r = e.currentTarget.getBoundingClientRect(); const id = hit(e.clientX - r.left, e.clientY - r.top); if (id) onSelect(id); }
        }}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          const step = 40;
          if (e.key === "+" || e.key === "=") zoom(1.25);
          else if (e.key === "-") zoom(0.8);
          else if (e.key === "0") reset();
          else if (e.key === "ArrowLeft") setView((v) => ({ ...v, x: v.x + step }));
          else if (e.key === "ArrowRight") setView((v) => ({ ...v, x: v.x - step }));
          else if (e.key === "ArrowUp") setView((v) => ({ ...v, y: v.y + step }));
          else if (e.key === "ArrowDown") setView((v) => ({ ...v, y: v.y - step }));
          else return;
          e.preventDefault();
        }}
      />
      <div className="absolute right-3 top-3 flex flex-col gap-1.5">
        {[["+", "Zoom in", () => zoom(1.25)], ["−", "Zoom out", () => zoom(0.8)], ["⌖", "Reset view", reset]].map(([g, l, f]) => (
          <button key={l as string} type="button" aria-label={l as string} title={l as string} onClick={f as () => void} className="grid size-9 place-items-center rounded-lg border border-line-strong bg-surface text-lg hover:bg-surface-2">{g as string}</button>
        ))}
      </div>
      <ul className="pointer-events-none absolute bottom-3 left-3 flex max-w-[calc(100%-1.5rem)] flex-wrap gap-x-4 gap-y-1 rounded-lg bg-surface/90 px-3 py-2 text-xs" aria-label="Legend">
        {types.map((t) => (
          <li key={t} className="flex items-center gap-1.5"><TypeGlyph type={t} size={12} />{typeLabel(t)}</li>
        ))}
      </ul>
      {hovered && <div className="pointer-events-none absolute left-3 top-3 max-w-[60%] rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-xs shadow-[var(--shadow)]"><span className="font-mono">{hovered.id}</span> {hovered.name}</div>}
      {nodes.length === 0 && <p className="absolute inset-0 grid place-items-center text-sm text-muted">No nodes to draw.</p>}
    </div>
  );
}
