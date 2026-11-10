"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { dateTime } from "@/lib/format";
import type { GraphNode, GraphVersion } from "@/lib/types";
import { Button, EmptyState, ErrorState, LoadingBlock, Select, TypeGlyph, cx } from "./ui";

export function GraphDiffView({ projectId, versions, onSelect }: { projectId: string; versions: GraphVersion[]; onSelect: (id: string) => void }) {
  const vs = versions.map((v) => v.version);
  const [a, setA] = useState(vs[vs.length - 2] ?? vs[0] ?? 0);
  const [b, setB] = useState(vs[vs.length - 1] ?? 0);
  const [asked, setAsked] = useState<[number, number] | null>(null);
  const diff = useQuery(() => api.graph.diff(projectId, asked![0], asked![1]), [projectId, asked?.[0], asked?.[1]], !!asked);

  if (versions.length < 2) return <EmptyState title="Only one version so far">Re-run the analysis after reviewing findings and the changes between versions appear here.</EmptyState>;

  const groups: Array<[string, GraphNode[], string]> = diff.data ? [
    ["Added", diff.data.added_nodes, "text-ok"], ["Removed", diff.data.removed_nodes, "text-danger"], ["Changed", diff.data.changed_nodes, "text-warn"],
  ] : [];
  return (
    <div className="space-y-6">
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); setAsked([a, b]); }}>
        <div><label htmlFor="va" className="eyebrow mb-1 block">From</label><Select id="va" value={a} onChange={(e) => setA(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.node_count.toLocaleString("en-US")} nodes</option>)}</Select></div>
        <div><label htmlFor="vb" className="eyebrow mb-1 block">To</label><Select id="vb" value={b} onChange={(e) => setB(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.created_at ? dateTime(v.created_at) : ""}</option>)}</Select></div>
        <Button type="submit" variant="primary" disabled={a === b}>Compare</Button>
        {a === b && <span className="text-sm text-muted">Pick two different versions.</span>}
      </form>
      {asked && diff.loading && <LoadingBlock label="Comparing versions" rows={2} />}
      {diff.error && <ErrorState error={diff.error} onRetry={diff.reload} />}
      {diff.data && (
        <div className="grid gap-6 md:grid-cols-3">
          {groups.map(([label, nodes, color]) => (
            <section key={label} aria-label={`${label} nodes`}>
              <h3 className={cx("display text-3xl", color)}>{label} <span className="font-sans text-base tnum">({nodes.length})</span></h3>
              {nodes.length === 0 ? <p className="mt-2 text-sm text-muted">None.</p> : (
                <ul className="mt-2 max-h-[420px] space-y-1 overflow-auto">
                  {nodes.slice(0, 300).map((n) => (
                    <li key={n.id}>
                      <button type="button" disabled={label === "Removed"} onClick={() => onSelect(n.id)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-surface-2 disabled:cursor-default disabled:hover:bg-transparent">
                        <TypeGlyph type={n.type} /><span className="min-w-0 truncate">{n.name}</span><span className="ml-auto font-mono text-[11px] text-muted">{n.id}</span>
                      </button>
                    </li>
                  ))}
                  {nodes.length > 300 && <li className="px-2 text-xs text-muted">and {nodes.length - 300} more</li>}
                </ul>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
