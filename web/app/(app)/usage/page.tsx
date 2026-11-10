"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useQuery } from "@/lib/hooks";
import { compact, dateTime, num, usd } from "@/lib/format";
import { Card, ErrorState, LoadingBlock, PageHeader, Select, cx } from "@/components/ui";

const LIMIT_LABELS: Record<string, string> = {
  monthly_usd: "Monthly model spend", projects: "Projects", max_lines_per_project: "Lines per project", concurrent_runs: "Concurrent runs", members: "Members",
  max_projects: "Projects", max_members: "Members", max_runs_per_month: "Runs this month", max_concurrent_runs: "Concurrent runs",
  max_usd_per_run: "Spend cap per run", monthly_usd_budget: "Monthly model spend", max_source_mb: "Source size (MB)",
};
/** The backend names limits `max_*` and consumption counters differently; this maps one to the other. */
const CONSUMED_AS: Record<string, string> = {
  max_projects: "projects", max_members: "members", max_runs_per_month: "runs_this_month",
  max_concurrent_runs: "active_runs", monthly_usd_budget: "usd_this_month",
};
const isMoney = (k: string) => k.endsWith("_usd") || k.endsWith("_usd_budget") || k.endsWith("usd_per_run");

export default function UsagePage() {
  const { workspace } = useAuth();
  const wid = workspace?.id;
  const [days, setDays] = useState(30);
  const usage = useQuery(() => api.workspaces.usage(wid!, { from: new Date(Date.now() - days * 86_400_000).toISOString() }), [wid, days], !!wid);
  const ent = useQuery(() => api.workspaces.entitlements(wid!), [wid], !!wid);

  const byDay = new Map<string, number>();
  usage.data?.runs.forEach((r) => { const d = r.at.slice(0, 10); byDay.set(d, (byDay.get(d) ?? 0) + r.usd); });
  const bars = [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b));
  const max = Math.max(0.0001, ...bars.map(([, v]) => v));

  return (
    <>
      <title>Usage and plan · Migration Agent</title>
      <PageHeader eyebrow={workspace?.name} title="Usage and plan">What your workspace has consumed, and the limits it runs under.</PageHeader>

      <section aria-labelledby="plan-h" className="rise rise-1 mb-10">
        <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          <Card className="p-6">
            <p className="eyebrow">Current plan</p>
            <h2 id="plan-h" className="display mt-1 text-5xl capitalize">{ent.data?.plan ?? "…"}</h2>
            <p className="mt-2 text-sm text-muted">Generous limits for internal and design-partner use.</p>
          </Card>
          <Card className="border-dashed border-line-strong bg-surface-2 p-6">
            <div className="flex items-center gap-2"><span className="rounded-full bg-warn-soft px-2.5 py-0.5 text-xs font-semibold text-warn">Billing not enabled yet</span></div>
            <p className="mt-3">There is nothing to pay and no card is stored. Usage is metered so you can see what a run costs, but no invoices are issued.</p>
            <p className="mt-2 text-sm text-muted">When paid plans launch you will be told before any limit changes, and nothing is charged without an explicit opt-in.</p>
          </Card>
        </div>
      </section>

      <section aria-labelledby="ent-h" className="mb-10">
        <h2 id="ent-h" className="display mb-4 text-3xl">Entitlements</h2>
        {ent.loading && !ent.data ? <LoadingBlock label="Loading entitlements" rows={2} /> : ent.error ? <ErrorState error={ent.error} onRetry={ent.reload} /> : ent.data && (
          <Card>
            <ul className="divide-y divide-line">
              {Object.entries(ent.data.limits).map(([k, limit]) => {
                const used = ent.data!.consumption[CONSUMED_AS[k] ?? k] ?? 0;
                const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
                const fmt = (n: number) => (isMoney(k) ? usd(n) : num(n));
                return (
                  <li key={k} className="grid gap-2 px-5 py-4 sm:grid-cols-[220px_1fr_auto] sm:items-center">
                    <span className="font-medium">{LIMIT_LABELS[k] ?? k.replace(/_/g, " ")}</span>
                    <div role="meter" aria-label={LIMIT_LABELS[k] ?? k} aria-valuemin={0} aria-valuemax={limit ?? undefined} aria-valuenow={used} className="h-2 overflow-hidden rounded-full bg-surface-2">
                      <div className={cx("h-full rounded-full", pct > 85 ? "bg-danger" : pct > 60 ? "bg-warn" : "bg-accent")} style={{ width: `${limit ? pct : 0}%` }} />
                    </div>
                    <span className="font-mono text-sm tnum">{fmt(used)} <span className="text-muted">/ {limit === null ? "unlimited" : fmt(limit)}</span></span>
                  </li>
                );
              })}
            </ul>
          </Card>
        )}
      </section>

      <section aria-labelledby="use-h">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <h2 id="use-h" className="display text-3xl">Usage</h2>
          <div><label htmlFor="range" className="eyebrow mr-2">Period</label><Select id="range" className="!w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></Select></div>
        </div>
        {usage.loading && !usage.data ? <LoadingBlock label="Loading usage" rows={3} /> : usage.error ? <ErrorState error={usage.error} onRetry={usage.reload} /> : usage.data && (
          <>
            <dl className="mb-6 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
              {[["Spend", usd(usage.data.totals.usd)], ["Runs", num(usage.data.totals.runs)], ["Input tokens", compact(usage.data.totals.input_tokens)], ["Output tokens", compact(usage.data.totals.output_tokens)]].map(([k, v]) => (
                <div key={k} className="bg-surface px-5 py-4"><dt className="eyebrow">{k}</dt><dd className="display mt-1 text-3xl tnum">{v}</dd></div>
              ))}
            </dl>
            {bars.length > 0 ? (
              <Card className="mb-6 p-5">
                <p className="eyebrow mb-3">Spend per day</p>
                <div role="img" aria-label={`Daily spend, ${bars.length} days with activity, highest ${usd(max)}`} className="flex h-32 items-end gap-1.5">
                  {bars.map(([d, v]) => <div key={d} title={`${d}: ${usd(v)}`} className="min-w-2 flex-1 rounded-t bg-accent" style={{ height: `${Math.max(3, (v / max) * 100)}%` }} />)}
                </div>
              </Card>
            ) : null}
            {usage.data.runs.length === 0 ? <p className="rounded-xl border border-dashed border-line-strong p-8 text-center text-muted">No runs in this period.</p> : (
              <Card className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <caption className="sr-only">Cost per run</caption>
                  <thead className="eyebrow border-b border-line"><tr><th scope="col" className="px-4 py-3 font-normal">When</th><th scope="col" className="px-4 py-3 font-normal">Project</th><th scope="col" className="px-4 py-3 font-normal">Type</th><th scope="col" className="px-4 py-3 text-right font-normal">Tokens in / out</th><th scope="col" className="px-4 py-3 text-right font-normal">Cost</th></tr></thead>
                  <tbody>
                    {usage.data.runs.map((r) => (
                      <tr key={r.run_id} className="border-b border-line last:border-0">
                        <td className="px-4 py-3">{dateTime(r.at)}</td><td className="px-4 py-3">{r.project_name ?? r.project_id.slice(0, 8)}</td><td className="px-4 py-3 font-mono">{r.type}</td>
                        <td className="px-4 py-3 text-right font-mono tnum">{compact(r.input_tokens)} / {compact(r.output_tokens)}</td><td className="px-4 py-3 text-right font-mono tnum">{usd(r.usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}
          </>
        )}
      </section>
    </>
  );
}
