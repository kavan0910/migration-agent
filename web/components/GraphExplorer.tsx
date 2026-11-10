"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useDebounced, useQuery } from "@/lib/hooks";
import { useGraphList } from "@/lib/useGraphList";
import { induced, neighbourhood, topByDegree } from "@/lib/graphFilter";
import { MAX_GRAPH_NODES } from "@/lib/graphLayout";
import { num, typeLabel } from "@/lib/format";
import { ConfidenceMeter, EmptyState, ErrorState, Input, LinkButton, LoadingBlock, Select, TypeGlyph, cx } from "./ui";
import { VirtualList } from "./VirtualList";
import { GraphCanvas } from "./GraphCanvas";
import { NodeDetail } from "./NodeDetail";
import { GraphDiffView } from "./GraphDiffView";
import { useProject } from "./ProjectContext";
import type { GraphNode } from "@/lib/types";

const TYPES = ["component", "endpoint", "data_entity", "business_rule", "external_dependency", "config"];
type Tab = "detail" | "graph" | "versions";

export function GraphExplorer() {
  const { project } = useProject();
  const router = useRouter();
  const params = useSearchParams();
  const selected = params.get("node");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [tab, setTab] = useState<Tab>(selected ? "detail" : "graph");
  const [focusMode, setFocusMode] = useState(true);
  const dq = useDebounced(q, 250);

  const versions = useQuery(() => api.graph.versions(project.id), [project.id]);
  const findings = useQuery(() => api.review.findings(project.id), [project.id]);
  const list = useGraphList(project.id, { version, type: type || undefined, q: dq.trim() || undefined });

  const select = (id: string) => {
    router.replace(`/projects/${project.id}/graph?node=${encodeURIComponent(id)}`, { scroll: false });
    setTab((t) => (t === "graph" ? "graph" : "detail"));
  };

  const view = useMemo(() => {
    const base = { nodes: list.nodes, edges: list.edges };
    if (focusMode && selected && list.nodes.some((n) => n.id === selected)) return { ...neighbourhood(base.nodes, base.edges, selected, 2, 150), capped: false, focused: true };
    const top = topByDegree(base.nodes, base.edges, MAX_GRAPH_NODES);
    return { ...induced(top, base.edges), capped: base.nodes.length > MAX_GRAPH_NODES, focused: false };
  }, [list.nodes, list.edges, focusMode, selected]);

  if (!versions.loading && versions.data && versions.data.items.length === 0 && !list.loading && list.total === 0 && !q && !type) {
    return <EmptyState title="No graph yet" action={<LinkButton href={`/projects/${project.id}`} variant="primary">Go to runs</LinkButton>}>The knowledge graph is built by the first successful analysis run.</EmptyState>;
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(300px,380px)_1fr]">
      <section aria-label="Node list" className="min-w-0">
        <div className="space-y-3">
          <div>
            <label htmlFor="gq" className="sr-only">Search nodes</label>
            <Input id="gq" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, id, file, behaviour" autoComplete="off" />
          </div>
          <div role="group" aria-label="Filter by type" className="flex flex-wrap gap-1.5">
            {[["", "All"], ...TYPES.map((t) => [t, typeLabel(t)])].map(([t, l]) => (
              <button key={t} type="button" aria-pressed={type === t} onClick={() => setType(t!)}
                className={cx("inline-flex min-h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium", type === t ? "border-ink bg-ink text-bg" : "border-line-strong hover:bg-surface-2")}>
                {t && <TypeGlyph type={t} size={11} />}{l}
              </button>
            ))}
          </div>
          {versions.data && versions.data.items.length > 1 && (
            <div className="flex items-center gap-2">
              <label htmlFor="gv" className="eyebrow">Version</label>
              <Select id="gv" className="!min-h-8 !w-auto py-0 text-xs" value={version ?? ""} onChange={(e) => setVersion(e.target.value ? Number(e.target.value) : undefined)}>
                <option value="">Latest</option>
                {versions.data.items.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
              </Select>
            </div>
          )}
          <p className="text-xs text-muted tnum" aria-live="polite">{list.loading ? "Loading" : `${num(list.total)} node${list.total === 1 ? "" : "s"}${list.nodes.length < list.total ? ` · ${num(list.nodes.length)} loaded` : ""} · most connected first`}</p>
        </div>

        <div className="mt-2 rounded-xl border border-line bg-surface">
          {list.loading && list.nodes.length === 0 ? <div className="p-3"><LoadingBlock label="Loading nodes" rows={6} /></div>
            : list.error ? <div className="p-3"><ErrorState error={list.error} onRetry={list.retry} /></div>
            : list.nodes.length === 0 ? <p className="p-8 text-center text-sm text-muted">No nodes match these filters.</p>
            : (
              <VirtualList<GraphNode>
                items={list.nodes} total={list.total} rowHeight={60} height="min(68dvh, 680px)" label="Graph nodes"
                getKey={(n) => n.id} selectedKey={selected} onSelect={(n) => select(n.id)}
                onEndReached={list.hasMore ? list.loadMore : undefined} loadingMore={list.loadingMore}
                renderRow={(n, st) => (
                  <div className={cx("flex h-full items-center gap-3 border-b border-line px-3", st.selected ? "bg-accent-soft" : "hover:bg-surface-2", st.active && "shadow-[inset_2px_0_0_var(--accent)]")}>
                    <TypeGlyph type={n.type} size={15} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{n.name}</p>
                      <p className="truncate font-mono text-[11px] text-muted">{n.id}{n.module ? ` · ${n.module.split("/").slice(-1)[0]}` : ""}</p>
                    </div>
                    <ConfidenceMeter value={n.confidence} showLabel={false} />
                  </div>
                )}
              />
            )}
        </div>
      </section>

      <section aria-label="Explorer" className="min-w-0">
        <div role="tablist" aria-label="Explorer views" className="mb-4 inline-flex rounded-lg border border-line-strong bg-surface p-1">
          {([["graph", "Graph"], ["detail", "Node detail"], ["versions", "Versions"]] as Array<[Tab, string]>).map(([t, l]) => (
            <button key={t} role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls={`panel-${t}`} tabIndex={tab === t ? 0 : -1} type="button" onClick={() => setTab(t)}
              onKeyDown={(e) => {
                const order: Tab[] = ["graph", "detail", "versions"];
                const i = order.indexOf(tab);
                if (e.key === "ArrowRight") { setTab(order[(i + 1) % 3]!); document.getElementById(`tab-${order[(i + 1) % 3]}`)?.focus(); }
                if (e.key === "ArrowLeft") { setTab(order[(i + 2) % 3]!); document.getElementById(`tab-${order[(i + 2) % 3]}`)?.focus(); }
              }}
              className={cx("rounded-md px-4 py-1.5 text-sm font-medium", tab === t ? "bg-ink text-bg" : "text-muted hover:text-ink")}>{l}</button>
          ))}
        </div>

        <div role="tabpanel" id="panel-graph" aria-labelledby="tab-graph" hidden={tab !== "graph"}>
          {tab === "graph" && (
            <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm">
                <p className="text-muted tnum">Showing {num(view.nodes.length)} of {num(list.total)} nodes{view.capped ? `. Capped at ${MAX_GRAPH_NODES} to stay responsive; narrow the filters to see others.` : "."}</p>
                {selected && <label className="flex cursor-pointer items-center gap-2"><input type="checkbox" className="size-4 accent-[var(--accent)]" checked={focusMode} onChange={(e) => setFocusMode(e.target.checked)} />Focus on selected neighbourhood</label>}
              </div>
              <div style={{ height: "min(68dvh, 680px)" }}>
                <GraphCanvas nodes={view.nodes} edges={view.edges} selectedId={selected} onSelect={select} />
              </div>
            </>
          )}
        </div>
        <div role="tabpanel" id="panel-detail" aria-labelledby="tab-detail" hidden={tab !== "detail"}>
          {tab === "detail" && (selected
            ? <NodeDetail key={selected} projectId={project.id} nodeId={selected} findings={findings.data?.items ?? []} onSelect={select} />
            : <EmptyState title="Select a node">Choose a node from the list or the graph to see its evidence, confidence and relationships.</EmptyState>)}
        </div>
        <div role="tabpanel" id="panel-versions" aria-labelledby="tab-versions" hidden={tab !== "versions"}>
          {tab === "versions" && (versions.data
            ? <GraphDiffView projectId={project.id} versions={versions.data.items} onSelect={select} />
            : <LoadingBlock label="Loading versions" rows={2} />)}
        </div>
      </section>
    </div>
  );
}
