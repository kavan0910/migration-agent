"use client";
import { useEffect, useReducer, useRef, useState } from "react";
import { api } from "@/lib/api";
import { emptyRunLive, overallPercent, reduceRunEvent } from "@/lib/runState";
import { TERMINAL_STATUSES, type Run, type RunEvent } from "@/lib/types";
import { errorMessage } from "@/lib/hooks";
import { duration, usd } from "@/lib/format";
import { Button, Card, Notice, StatusPill } from "./ui";
import { useToast } from "./Toast";

/** Live view of one run: subscribes to the SSE stream and mirrors progress, spend and log. */
export function RunPanel({ run, onFinished, onDismiss }: { run: Run; onFinished: (status: string) => void; onDismiss?: () => void }) {
  const [live, dispatch] = useReducer(reduceRunEvent, undefined, emptyRunLive);
  const [conn, setConn] = useState<"connecting" | "open" | "lost">("connecting");
  const [cancelling, setCancelling] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const done = useRef(onFinished);
  const toast = useToast();
  useEffect(() => { done.current = onFinished; });

  useEffect(() => {
    const ctl = new AbortController();
    let finished = false;
    const onEvent = (e: RunEvent) => {
      dispatch(e);
      if (e.type === "status" && TERMINAL_STATUSES.includes(e.status) && !finished) {
        finished = true;
        done.current(e.status);
      }
    };
    const connect = async () => {
      for (let attempt = 0; attempt < 5 && !ctl.signal.aborted && !finished; attempt++) {
        try {
          setConn("open");
          await api.runs.stream(run.id, onEvent, ctl.signal);
          if (finished || ctl.signal.aborted) return;
        } catch {
          if (ctl.signal.aborted) return;
        }
        setConn("lost");
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      }
      // Fall back to a single status read so the panel never hangs on a dead stream.
      if (!finished && !ctl.signal.aborted) {
        const r = await api.runs.get(run.id).catch(() => null);
        if (r && TERMINAL_STATUSES.includes(r.status)) onEvent({ type: "status", status: r.status, error: r.error });
      }
    };
    void connect();
    return () => ctl.abort();
  }, [run.id]);

  const status = live.status ?? run.status;
  const active = !TERMINAL_STATUSES.includes(status);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  const pct = status === "succeeded" ? 100 : overallPercent(live);
  const cost = Math.max(live.cost, run.cost_usd);

  async function cancel() {
    setCancelling(true);
    try { await api.runs.cancel(run.id); toast("Cancelling the run"); } catch (e) { toast(errorMessage(e), "error"); setCancelling(false); }
  }

  return (
    <Card className="rise p-5 sm:p-6" aria-label="Run progress">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3"><h2 className="display text-3xl">{active ? "Run in progress" : "Latest run"}</h2><StatusPill status={status} /></div>
        {active ? <Button onClick={cancel} loading={cancelling}>Cancel run</Button> : onDismiss && <Button variant="primary" onClick={onDismiss}>Start another run</Button>}
      </div>
      <div className="mt-5" role="progressbar" aria-label="Overall progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <div className="h-2 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} /></div>
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-4 text-sm">
        <div><dt className="eyebrow">Spend</dt><dd className="font-mono tnum">{usd(cost, 4)}{run.max_usd ? <span className="text-muted"> / {usd(run.max_usd, 2)}</span> : null}</dd></div>
        <div><dt className="eyebrow">Elapsed</dt><dd className="font-mono tnum">{duration(run.started_at ?? run.created_at, active ? new Date(now).toISOString() : run.finished_at)}</dd></div>
        <div><dt className="eyebrow">Stream</dt><dd>{active ? (conn === "lost" ? "Reconnecting" : "Live") : "Closed"}</dd></div>
      </dl>
      <ul className="mt-5 space-y-2" aria-label="Stages">
        {live.stageOrder.length === 0 && active && <li className="text-sm text-muted">Waiting for the worker to pick this up.</li>}
        {live.stageOrder.map((k) => {
          const s = live.stages[k]!;
          const complete = s.done >= s.total;
          return (
            <li key={k} className="flex items-center gap-3 text-sm">
              <span className={`grid size-5 place-items-center rounded-full text-[10px] ${complete ? "bg-ok text-white dark:text-black" : "border border-accent text-accent"}`} aria-hidden="true">{complete ? "✓" : ""}</span>
              <span className="w-20 font-mono capitalize">{k}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2"><span className="block h-full bg-accent transition-[width] duration-500" style={{ width: `${(s.done / Math.max(1, s.total)) * 100}%` }} /></span>
              <span className="w-12 text-right font-mono text-xs tnum text-muted">{s.done}/{s.total}</span>
            </li>
          );
        })}
      </ul>
      {live.error && <div className="mt-4"><Notice tone={status === "stopped_budget" ? "warn" : "danger"}>{live.error}</Notice></div>}
      {live.logs.length > 0 && (
        <details className="mt-5">
          <summary className="cursor-pointer text-sm font-medium">Log ({live.logs.length})</summary>
          <ol className="mt-2 max-h-48 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs leading-6">
            {live.logs.map((l, i) => <li key={i}><span className="text-muted">{l.level}</span> {l.message}</li>)}
          </ol>
        </details>
      )}
    </Card>
  );
}
