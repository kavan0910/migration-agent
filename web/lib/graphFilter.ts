import type { Confidence, GraphEdge, GraphNode } from "./types";

export const CONFIDENCE_RANK: Record<Confidence, number> = { low: 0, medium: 1, high: 2 };

export interface NodeFilter {
  type?: string | null;
  q?: string | null;
  minConfidence?: Confidence | null;
  flaggedOnly?: boolean;
}

function haystack(n: GraphNode): string {
  return [n.id, n.name, n.module, n.qualified_symbol, n.responsibility, n.type].filter(Boolean).join(" ").toLowerCase();
}

/** All whitespace separated terms must appear (AND), case-insensitive. */
export function matchesQuery(n: GraphNode, q: string | null | undefined): boolean {
  const terms = (q ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const h = haystack(n);
  return terms.every((t) => h.includes(t));
}

export function filterNodes<T extends GraphNode>(nodes: readonly T[], f: NodeFilter): T[] {
  const min = f.minConfidence ? CONFIDENCE_RANK[f.minConfidence] : -1;
  return nodes.filter((n) => {
    if (f.type && n.type !== f.type) return false;
    if (min >= 0 && CONFIDENCE_RANK[n.confidence] < min) return false;
    if (f.flaggedOnly && !(n.quirk || n.suspected_bug)) return false;
    return matchesQuery(n, f.q);
  });
}

export function countByType(nodes: readonly GraphNode[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const n of nodes) out[n.type] = (out[n.type] ?? 0) + 1;
  return out;
}

/** Keep only edges whose both endpoints are present. */
export function induced(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const ids = new Set(nodes.map((n) => n.id));
  return { nodes: [...nodes], edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
}

export function degrees(edges: readonly GraphEdge[]): Map<string, number> {
  const d = new Map<string, number>();
  for (const e of edges) {
    d.set(e.source, (d.get(e.source) ?? 0) + 1);
    d.set(e.target, (d.get(e.target) ?? 0) + 1);
  }
  return d;
}

/** Most connected nodes first (ties broken by id for determinism), capped at `max`. */
export function topByDegree(nodes: readonly GraphNode[], edges: readonly GraphEdge[], max: number): GraphNode[] {
  const d = degrees(edges);
  return [...nodes]
    .sort((a, b) => (d.get(b.id) ?? 0) - (d.get(a.id) ?? 0) || a.id.localeCompare(b.id))
    .slice(0, Math.max(0, max));
}

/** Breadth-first neighbourhood of `centerId` up to `depth` hops, never more than `max` nodes. */
export function neighbourhood(
  nodes: readonly GraphNode[], edges: readonly GraphEdge[], centerId: string, depth: number, max: number,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  if (!byId.has(centerId)) return { nodes: [], edges: [] };
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    (adj.get(e.source) ?? adj.set(e.source, []).get(e.source)!).push(e.target);
    (adj.get(e.target) ?? adj.set(e.target, []).get(e.target)!).push(e.source);
  }
  const seen = new Set([centerId]);
  let frontier = [centerId];
  for (let d = 0; d < depth && seen.size < max; d++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const nb of adj.get(id) ?? []) {
        if (seen.has(nb) || !byId.has(nb)) continue;
        if (seen.size >= max) break;
        seen.add(nb);
        next.push(nb);
      }
    }
    frontier = next;
  }
  return induced([...seen].map((id) => byId.get(id)!), edges);
}
