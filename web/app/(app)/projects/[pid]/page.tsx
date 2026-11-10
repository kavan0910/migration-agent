"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { dateTime, duration, num, usd } from "@/lib/format";
import { TERMINAL_STATUSES, type Run } from "@/lib/types";
import { Card, EmptyState, ErrorState, LinkButton, LoadingBlock, StatusPill } from "@/components/ui";
import { RunLauncher } from "@/components/RunLauncher";
import { RunPanel } from "@/components/RunPanel";
import { useProject } from "@/components/ProjectContext";

export default function Overview() {
  const { project, reload } = useProject();
  const runs = useQuery(() => api.runs.list(project.id, { limit: 50 }), [project.id]);
  const [current, setCurrent] = useState<Run | null>(null);

  const list = runs.data?.items ?? [];
  const active = current ?? list.find((r) => !TERMINAL_STATUSES.includes(r.status)) ?? null;
  const hasGraph = (project.node_count ?? 0) > 0;
  const noSource = project.source_kind === "none";

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_340px]">
      <div className="min-w-0 space-y-8">
        {active ? (
          <RunPanel key={active.id} run={active} onDismiss={() => { setCurrent(null); runs.reload(); }} onFinished={() => { setCurrent(active); reload(); runs.reload(); }} />
        ) : (
          <RunLauncher projectId={project.id} hasGraph={hasGraph} disabled={noSource ? "Add a source before starting a run." : null} onStarted={(r) => { setCurrent(r); runs.reload(); reload(); }} />
        )}
        <section aria-labelledby="runs-h">
          <h2 id="runs-h" className="display mb-4 text-3xl">Run history</h2>
          {runs.loading && !runs.data ? <LoadingBlock label="Loading runs" rows={3} /> : runs.error ? <ErrorState error={runs.error} onRetry={runs.reload} /> : list.length === 0 ? (
            <EmptyState title="No runs yet">Start your first analysis above. Small services finish in under a minute.</EmptyState>
          ) : (
            <Card className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-sm">
                <caption className="sr-only">Runs for this project, newest first</caption>
                <thead className="eyebrow border-b border-line"><tr><th scope="col" className="px-4 py-3 font-normal">Started</th><th scope="col" className="px-4 py-3 font-normal">Type</th><th scope="col" className="px-4 py-3 font-normal">Status</th><th scope="col" className="px-4 py-3 text-right font-normal">Duration</th><th scope="col" className="px-4 py-3 text-right font-normal">Cost</th></tr></thead>
                <tbody>
                  {list.map((r) => (
                    <tr key={r.id} className="border-b border-line last:border-0">
                      <td className="px-4 py-3">{dateTime(r.started_at ?? r.created_at)}</td>
                      <td className="px-4 py-3"><span className="font-mono">{r.type}</span> <span className="text-muted">· {r.model_profile}{r.incremental ? " · incremental" : ""}</span></td>
                      <td className="px-4 py-3"><StatusPill status={r.status} /></td>
                      <td className="px-4 py-3 text-right font-mono tnum">{r.started_at ? duration(r.started_at, r.finished_at) : "n/a"}</td>
                      <td className="px-4 py-3 text-right font-mono tnum">{usd(r.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </section>
      </div>

      <aside className="space-y-4" aria-label="Project summary">
        <Card className="p-5">
          <p className="eyebrow">Source</p>
          <p className="mt-1 break-all font-mono text-sm">{noSource ? "None connected" : project.source_ref}</p>
          <p className="text-xs text-muted">{project.source_kind === "git" ? "Git repository" : project.source_kind === "upload" ? "Uploaded archive" : ""}</p>
        </Card>
        <Card className="p-5">
          <p className="eyebrow">Results</p>
          <dl className="mt-2 grid grid-cols-2 gap-3">
            <div><dt className="text-xs text-muted">Nodes</dt><dd className="display text-3xl tnum">{num(project.node_count)}</dd></div>
            <div><dt className="text-xs text-muted">Open findings</dt><dd className="display text-3xl tnum">{num(project.open_findings)}</dd></div>
          </dl>
          {hasGraph ? (
            <div className="mt-4 flex flex-col gap-2">
              <LinkButton href={`/projects/${project.id}/document`} variant="primary">Read the document</LinkButton>
              <LinkButton href={`/projects/${project.id}/graph`}>Explore the graph</LinkButton>
              <LinkButton href={`/projects/${project.id}/findings`}>Review findings</LinkButton>
            </div>
          ) : <p className="mt-3 text-sm text-muted">Results appear here after the first successful run.</p>}
        </Card>
        <p className="px-1 text-xs text-muted">Costs are billed to your workspace allowance. See <Link href="/usage" className="underline">usage and plan</Link>.</p>
      </aside>
    </div>
  );
}
