"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { errorMessage, useDebounced, useQuery } from "@/lib/hooks";
import { num, usd } from "@/lib/format";
import type { ModelProfile, Run, RunType } from "@/lib/types";
import { Button, Card, Field, Input, Notice, Select, Skeleton, cx } from "./ui";

const PROFILES: Array<{ id: ModelProfile; label: string; blurb: string }> = [
  { id: "fast", label: "Fast", blurb: "Cheapest, good for a first look" },
  { id: "balanced", label: "Balanced", blurb: "Recommended for most services" },
  { id: "best", label: "Best", blurb: "Highest quality, highest cost" },
];

export function RunLauncher({ projectId, disabled, hasGraph, onStarted }: { projectId: string; disabled?: string | null; hasGraph: boolean; onStarted: (r: Run) => void }) {
  const [type, setType] = useState<RunType>("analyze");
  const [profile, setProfile] = useState<ModelProfile>("balanced");
  const [incremental, setIncremental] = useState(false);
  const [budget, setBudget] = useState("1.00");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dp = useDebounced(profile, 150);
  const est = useQuery(() => api.projects.estimate(projectId, { model_profile: dp, incremental, type }), [projectId, dp, incremental, type], !disabled);

  const maxUsd = Number(budget);
  const budgetBad = budget.trim() !== "" && !(maxUsd > 0);
  const overBudget = !!est.data && maxUsd > 0 && est.data.usd > maxUsd;
  const canStart = !disabled && !!est.data?.allowed && !budgetBad && !busy && !est.loading;

  async function start() {
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.runs.start(projectId, { type, model_profile: profile, max_usd: budget.trim() === "" ? null : maxUsd, incremental }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-5 sm:p-6">
      <h2 className="display text-3xl">Start a run</h2>
      <p className="mt-1 text-sm text-muted">You always see the estimate first. A budget stops the run gracefully if it is reached.</p>

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <Field label="Run type" htmlFor="rtype">
          <Select id="rtype" value={type} onChange={(e) => setType(e.target.value as RunType)}>
            <option value="analyze">Analyze: read the code</option>
            <option value="verify" >Verify: compare with your docs</option>
            <option value="reanalyze" disabled={!hasGraph}>Re-analyze: refresh after review</option>
          </Select>
        </Field>
        <Field label="Budget cap (USD)" htmlFor="budget" error={budgetBad ? "Enter an amount above zero, or leave empty for no cap." : null} hint="Empty means no cap beyond your plan.">
          <Input id="budget" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} aria-invalid={budgetBad} className="font-mono" />
        </Field>
      </div>

      <fieldset className="mt-5">
        <legend className="mb-2 text-sm font-medium">Model profile</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {PROFILES.map((p) => (
            <label key={p.id} className={cx("cursor-pointer rounded-lg border p-3 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent", profile === p.id ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-surface-2")}>
              <input type="radio" name="profile" value={p.id} checked={profile === p.id} onChange={() => setProfile(p.id)} className="sr-only" />
              <span className="block text-sm font-semibold">{p.label}</span>
              <span className="block text-xs text-muted">{p.blurb}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="mt-5 flex min-h-10 cursor-pointer items-start gap-3 text-sm">
        <input type="checkbox" checked={incremental} onChange={(e) => setIncremental(e.target.checked)} className="mt-1 size-4 accent-[var(--accent)]" />
        <span><span className="font-medium">Incremental</span><span className="block text-muted">Only re-read code that changed since the last run. Unchanged code makes no model calls.</span></span>
      </label>

      <div className="mt-6 rounded-xl border border-line bg-surface-2 p-4" aria-live="polite">
        <p className="eyebrow mb-2">Estimate before you start</p>
        {disabled ? <p className="text-sm">{disabled}</p> : est.loading && !est.data ? <Skeleton className="h-10" /> : est.error ? (
          <p className="text-sm text-danger">Could not estimate: {est.error.message}</p>
        ) : est.data ? (
          <>
            <div className="flex flex-wrap items-baseline gap-x-8 gap-y-1">
              <p><span className="display text-4xl tnum">{usd(est.data.usd)}</span> <span className="text-sm text-muted">estimated</span></p>
              <p className="text-sm"><span className="font-mono tnum">{num(est.data.chunks)}</span> <span className="text-muted">chunks to read</span></p>
            </div>
            <p className={cx("mt-3 inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium", est.data.allowed ? "bg-ok-soft text-ok" : "bg-danger-soft text-danger")}>
              <span aria-hidden="true">{est.data.allowed ? "✓" : "✕"}</span>{est.data.allowed ? "Allowed on your plan" : "Not allowed"}
            </p>
            {est.data.reason && <p className="mt-2 text-sm">{est.data.reason}</p>}
            {overBudget && est.data.allowed && <p className="mt-2 text-sm text-warn">Your budget cap ({usd(maxUsd)}) is below the estimate. The run will stop early and give a partial result.</p>}
          </>
        ) : null}
      </div>

      {error && <div className="mt-4"><Notice tone="danger">{error}</Notice></div>}
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="primary" onClick={start} disabled={!canStart} loading={busy}>Start run</Button>
        {!canStart && !busy && est.data && !est.data.allowed && <span className="text-sm text-muted">Resolve the reason above to continue.</span>}
      </div>
    </Card>
  );
}
