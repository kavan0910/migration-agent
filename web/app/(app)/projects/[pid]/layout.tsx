"use client";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { ErrorState, LoadingBlock, StatusPill, cx, EmptyState, LinkButton } from "@/components/ui";
import { ProjectCtx } from "@/components/ProjectContext";
import { ApiError } from "@/lib/api";

const TABS = [
  { seg: "", label: "Overview" },
  { seg: "/document", label: "Document" },
  { seg: "/graph", label: "Graph" },
  { seg: "/findings", label: "Findings" },
  { seg: "/settings", label: "Settings" },
];

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const { pid } = useParams<{ pid: string }>();
  const path = usePathname();
  const q = useQuery((s) => { void s; return api.projects.get(pid); }, [pid]);
  if (q.loading && !q.data) return <LoadingBlock label="Loading project" rows={3} />;
  if (q.error && (q.error as ApiError).status === 404) {
    return <EmptyState title="Project not found" action={<LinkButton href="/dashboard" variant="primary">Back to projects</LinkButton>}>It may have been deleted, or it belongs to another workspace.</EmptyState>;
  }
  if (q.error || !q.data) return <ErrorState error={q.error ?? new Error("Not found")} onRetry={q.reload} title="Could not load project" />;
  const p = q.data;
  const base = `/projects/${p.id}`;
  return (
    <ProjectCtx.Provider value={{ project: p, reload: q.reload, setProject: (np) => q.setData(np) }}>
      <nav aria-label="Breadcrumb" className="mb-4 text-sm text-muted"><Link href="/dashboard" className="hover:text-ink hover:underline">Projects</Link> <span aria-hidden="true">/</span> <span className="text-ink">{p.name}</span></nav>
      <div className="mb-8 flex flex-wrap items-center justify-between gap-3">
        <h1 className="display min-w-0 break-words text-4xl sm:text-5xl">{p.name}</h1>
        <StatusPill status={p.last_run_status} />
      </div>
      <nav aria-label="Project sections" className="-mx-4 mb-8 overflow-x-auto border-b border-line px-4 sm:mx-0 sm:px-0">
        <ul className="flex min-w-max gap-1">
          {TABS.map((t) => {
            const href = base + t.seg;
            const active = t.seg === "" ? path === base : path.startsWith(href);
            return (
              <li key={t.seg}>
                <Link href={href} aria-current={active ? "page" : undefined}
                  className={cx("block border-b-2 px-4 py-3 text-sm font-medium", active ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink")}>
                  {t.label}
                  {t.seg === "/findings" && p.open_findings ? <span className="ml-2 rounded-full bg-danger px-1.5 py-0.5 text-[11px] text-white dark:text-black">{p.open_findings}</span> : null}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      {children}
    </ProjectCtx.Provider>
  );
}
