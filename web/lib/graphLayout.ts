import type { GraphEdge, GraphNode } from "./types";

export interface Point {
  x: number;
  y: number;
}
export interface LayoutOptions {
  iterations?: number;
  width?: number;
  height?: number;
  /** Node ids that should stay near the centre (e.g. the selected node). */
  focusId?: string | null;
}

/** Deterministic string hash in [0, 1). */
function hash01(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}

/**
 * Small deterministic force-directed layout. O(n^2) repulsion, so callers cap n (see MAX_GRAPH_NODES).
 * Initial positions are placed on a ring per node type, so the same input always yields the same picture.
 */
export function layoutGraph(nodes: readonly GraphNode[], edges: readonly GraphEdge[], opts: LayoutOptions = {}): Map<string, Point> {
  const n = nodes.length;
  const out = new Map<string, Point>();
  if (n === 0) return out;
  const W = opts.width ?? 1000;
  const H = opts.height ?? 700;
  const iterations = opts.iterations ?? (n > 300 ? 90 : 160);
  const types = [...new Set(nodes.map((x) => x.type))].sort();
  const x = new Float64Array(n);
  const y = new Float64Array(n);
  const vx = new Float64Array(n);
  const vy = new Float64Array(n);
  const index = new Map<string, number>();
  nodes.forEach((node, i) => {
    index.set(node.id, i);
    const sector = types.indexOf(node.type) / types.length;
    const ang = (sector + hash01(node.id) / types.length) * Math.PI * 2;
    const r = (0.25 + 0.6 * hash01(node.id + "r")) * Math.min(W, H) * 0.5;
    x[i] = W / 2 + Math.cos(ang) * r;
    y[i] = H / 2 + Math.sin(ang) * r;
  });
  const links: Array<[number, number]> = [];
  for (const e of edges) {
    const a = index.get(e.source);
    const b = index.get(e.target);
    if (a !== undefined && b !== undefined && a !== b) links.push([a, b]);
  }
  const focus = opts.focusId ? index.get(opts.focusId) : undefined;
  const k = Math.sqrt((W * H) / n) * 0.8; // ideal edge length
  for (let it = 0; it < iterations; it++) {
    const cool = 1 - it / iterations;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = x[i]! - x[j]!;
        let dy = y[i]! - y[j]!;
        let d2 = dx * dx + dy * dy;
        if (d2 > k * k * 9) continue;
        if (d2 < 0.01) {
          dx = hash01(nodes[i]!.id) - 0.5;
          dy = hash01(nodes[j]!.id) - 0.5;
          d2 = dx * dx + dy * dy + 0.01;
        }
        const f = (k * k) / d2;
        vx[i]! += dx * f * 0.02;
        vy[i]! += dy * f * 0.02;
        vx[j]! -= dx * f * 0.02;
        vy[j]! -= dy * f * 0.02;
      }
    }
    for (const [a, b] of links) {
      const dx = x[b]! - x[a]!;
      const dy = y[b]! - y[a]!;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = ((d - k) / d) * 0.08;
      vx[a]! += dx * f;
      vy[a]! += dy * f;
      vx[b]! -= dx * f;
      vy[b]! -= dy * f;
    }
    for (let i = 0; i < n; i++) {
      // gentle gravity to centre
      vx[i]! += (W / 2 - x[i]!) * 0.004;
      vy[i]! += (H / 2 - y[i]!) * 0.004;
      if (i === focus) {
        vx[i]! += (W / 2 - x[i]!) * 0.1;
        vy[i]! += (H / 2 - y[i]!) * 0.1;
      }
      const maxStep = 40 * cool + 1;
      x[i]! += Math.max(-maxStep, Math.min(maxStep, vx[i]!));
      y[i]! += Math.max(-maxStep, Math.min(maxStep, vy[i]!));
      vx[i]! *= 0.55;
      vy[i]! *= 0.55;
    }
  }
  nodes.forEach((node, i) => out.set(node.id, { x: x[i]!, y: y[i]! }));
  return out;
}

export const MAX_GRAPH_NODES = 400;
