"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export interface Query<T> {
  data: T | undefined;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
  setData: (v: T | ((prev: T | undefined) => T | undefined)) => void;
}

/** Tiny data hook: runs `fn` when deps change, ignores stale responses, exposes reload. */
export function useQuery<T>(fn: (signal: AbortSignal) => Promise<T>, deps: readonly unknown[], enabled = true): Query<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });
  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController();
    let live = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    fnRef.current(ctl.signal).then(
      (v) => { if (live) { setData(v); setLoading(false); } },
      (e: unknown) => {
        if (!live || (e instanceof DOMException && e.name === "AbortError")) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setLoading(false);
      },
    );
    return () => { live = false; ctl.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData };
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong.";
}
