"use client";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useQuery } from "@/lib/hooks";
import { num, relTime, usd } from "@/lib/format";
import { Card, EmptyState, ErrorState, LinkButton, LoadingBlock, PageHeader, StatusPill } from "@/components/ui";

export default function Dashboard() {
  const { workspace } = useAuth();
  const wid = workspace?.id;
  const projects = useQuery(() => api.projects.list(wid!, { limit: 200 }), [wid], !!wid);
  const ent = useQuery(() => api.workspaces.entitlements(wid!), [wid], !!wid);
  const canCreate = workspace?.role !== "viewer";

  const items = projects.data?.items ?? [];
  const open = items.reduce((a, p) => a + (p.open_findings ?? 0), 0);
  return (
    <>
      <title>Projects · Migration Agent</title>
      <PageHeader eyebrow={workspace?.name ?? "Workspace"} title="Projects" actions={canCreate && <LinkButton href="/projects/new" variant="primary">New project</LinkButton>}>
        Each project is one legacy service. Open one to run an analysis, read the document and review findings.
      </PageHeader>

      {projects.loading && !projects.data ? <LoadingBlock label="Loading projects" rows={4} /> : projects.error ? (
        <ErrorState error={projects.error} onRetry={projects.reload} title="Could not load projects" />
      ) : items.length === 0 ? (
        <EmptyState title="No projects yet" action={canCreate ? <LinkButton href="/projects/new" variant="primary">Connect a repository</LinkButton> : undefined}>
          {canCreate ? "Connect a git repository or upload an archive to get your first understanding document." : "Ask an owner or admin to create a project."}
        </EmptyState>
      ) : (
        <>
          <dl className="rise rise-1 mb-8 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
            {[
              ["Projects", num(items.length)],
              ["Open findings", num(open)],
              ["Spend, last 30 days", ent.data ? usd(ent.data.consumption.monthly_usd ?? 0) : "…"],
              ["Plan", ent.data?.plan ?? "…"],
            ].map(([k, v]) => (
              <div key={k} className="bg-surface px-5 py-4"><dt className="eyebrow">{k}</dt><dd className="display mt-1 text-3xl tnum capitalize">{v}</dd></div>
            ))}
          </dl>
          <ul className="grid gap-4 md:grid-cols-2" aria-label="Projects">
            {items.map((p, i) => (
              <li key={p.id} className={`rise rise-${Math.min(i + 1, 4)}`}>
                <Card className="group relative h-full p-5 transition-colors hover:border-accent">
                  <div className="flex items-start justify-between gap-3">
                    <h2 className="min-w-0 break-words text-lg font-semibold leading-snug">
                      <Link href={`/projects/${p.id}`} className="after:absolute after:inset-0 after:content-[''] focus-visible:after:rounded-xl">{p.name}</Link>
                    </h2>
                    <StatusPill status={p.last_run_status} />
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-muted" title={p.source_ref ?? undefined}>
                    {p.source_kind === "none" ? "No source connected" : `${p.source_kind === "git" ? "git" : "upload"} · ${p.source_ref}`}
                  </p>
                  <dl className="mt-5 flex gap-8 text-sm">
                    <div><dt className="eyebrow">Nodes</dt><dd className="tnum font-medium">{num(p.node_count)}</dd></div>
                    <div><dt className="eyebrow">Open findings</dt><dd className={`tnum font-medium ${p.open_findings ? "text-danger" : ""}`}>{num(p.open_findings)}</dd></div>
                    <div><dt className="eyebrow">Updated</dt><dd className="font-medium">{relTime(p.updated_at ?? p.created_at)}</dd></div>
                  </dl>
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
