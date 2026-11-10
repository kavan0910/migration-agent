"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { GraphEdge, GraphNode } from "./types";

export const PAGE = 200;

export interface GraphList {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total: number;
  loading: boolean;
  loadingMore: boolean;
  error: Error | null;
  hasMore: boolean;
  loadMore: () => void;
  retry: () => void;
}

/** Server-filtered, paged node list. Changing any filter resets the list; stale responses are discarded. */
export function useGraphList(pid: string, filters: { version?: number; type?: string; q?: string }): GraphList {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const gen = useRef(0);
  const busy = useRef(false);
  const loaded = useRef(0);
  const [tick, setTick] = useState(0);
  const { version, type, q } = filters;

  const fetchPage = useCallback(async (offset: number, g: number) => {
    const page = await api.graph.page(pid, { version, type, q, limit: PAGE, offset });
    if (g !== gen.current) return;
    setTotal(page.total);
    setNodes((prev) => (offset === 0 ? page.nodes : [...prev, ...page.nodes]));
    setEdges((prev) => {
      const base = offset === 0 ? [] : prev;
      const seen = new Set(base.map((e) => e.id));
      return [...base, ...page.edges.filter((e) => !seen.has(e.id))];
    });
    loaded.current = offset + page.nodes.length;
  }, [pid, version, type, q]);

  useEffect(() => {
    const g = ++gen.current;
    busy.current = true;
    loaded.current = 0;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    fetchPage(0, g).catch((e: unknown) => { if (g === gen.current) setError(e instanceof Error ? e : new Error(String(e))); })
      .finally(() => { if (g === gen.current) { setLoading(false); busy.current = false; } });
  }, [fetchPage, tick]);

  const hasMore = nodes.length < total;
  const loadMore = useCallback(() => {
    if (busy.current || loaded.current >= total) return;
    busy.current = true;
    const g = gen.current;
    setLoadingMore(true);
    fetchPage(loaded.current, g).catch((e: unknown) => { if (g === gen.current) setError(e instanceof Error ? e : new Error(String(e))); })
      .finally(() => { if (g === gen.current) { setLoadingMore(false); busy.current = false; } });
  }, [fetchPage, total]);

  return { nodes, edges, total, loading, loadingMore, error, hasMore, loadMore, retry: () => setTick((t) => t + 1) };
}
