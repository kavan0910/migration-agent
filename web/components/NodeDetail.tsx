"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { errorMessage, useQuery } from "@/lib/hooks";
import { evidenceRef, relTime } from "@/lib/format";
import type { Evidence, Finding, GraphEdge, GraphNode } from "@/lib/types";
import Link from "next/link";
import { Button, ConfidenceMeter, ErrorState, LoadingBlock, Notice, SeverityTag, Textarea, TypeTag } from "./ui";
import { useToast } from "./Toast";

export function EvidenceQuote({ ev }: { ev: Evidence }) {
  return (
    <figure className="quote overflow-hidden">
      <figcaption className="flex items-center justify-between gap-3 border-b border-line px-3 py-1.5 font-mono text-xs text-accent">
        <span className="min-w-0 break-all">{evidenceRef(ev)}</span>
        {ev.symbol && <span className="shrink-0 text-muted">{ev.symbol}</span>}
      </figcaption>
      <pre className="m-0 px-3 py-2">{ev.quote}</pre>
    </figure>
  );
}

const CONFIDENCE_WHY: Record<string, string> = {
  low: "Confidence is derived from evidence, not claimed by the model. Low means the quote supports the node only weakly or comes from a single static fact.",
  medium: "Medium means at least one verified quote supports the claim, but no corroborating second source was found.",
  high: "High means multiple verified quotes agree. No node reaches this by model assertion alone.",
};

export function NodeDetail({ projectId, nodeId, findings, onSelect }: { projectId: string; nodeId: string; findings: Finding[]; onSelect: (id: string) => void }) {
  const detail = useQuery(() => api.graph.node(projectId, nodeId), [projectId, nodeId]);
  const comments = useQuery(() => api.review.comments(projectId, nodeId), [projectId, nodeId]);
  const toast = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  if (detail.loading && !detail.data) return <LoadingBlock label="Loading node" rows={3} />;
  if (detail.error || !detail.data) return <ErrorState error={detail.error ?? new Error("Not found")} onRetry={detail.reload} title="Could not load this node" />;
  const { node, edges, neighbours } = detail.data;
  const byId = new Map<string, GraphNode>(neighbours.map((n) => [n.id, n]));
  const out = edges.filter((e) => e.source === node.id);
  const inn = edges.filter((e) => e.target === node.id);
  const related = findings.filter((f) => f.item_ids.includes(node.id));

  async function addComment(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api.review.comment(projectId, nodeId, text.trim());
      setText("");
      comments.reload();
      toast("Comment added");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  const rel = (list: GraphEdge[], dir: "out" | "in") => (
    <ul className="space-y-1.5">
      {list.map((e) => {
        const otherId = dir === "out" ? e.target : e.source;
        const other = byId.get(otherId);
        return (
          <li key={e.id} className="flex flex-wrap items-center gap-x-2 text-sm">
            <span className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px]">{e.type}</span>
            <span aria-hidden="true" className="text-muted">{dir === "out" ? "→" : "←"}</span>
            <button type="button" onClick={() => onSelect(otherId)} className="min-w-0 truncate text-left font-medium text-accent underline-offset-4 hover:underline">{other?.name ?? otherId}</button>
            <ConfidenceMeter value={e.confidence} showLabel={false} />
          </li>
        );
      })}
    </ul>
  );

  return (
    <article aria-label={`Node ${node.name}`} className="space-y-6">
      <header>
        <div className="flex flex-wrap items-center gap-3"><TypeTag type={node.type} /><span className="font-mono text-xs text-muted">{node.id}</span>
          {node.quirk && <span className="rounded bg-warn-soft px-2 py-0.5 text-xs font-medium text-warn">quirk</span>}
          {node.suspected_bug && <span className="rounded bg-danger-soft px-2 py-0.5 text-xs font-medium text-danger">suspected bug</span>}</div>
        <h2 className="display mt-2 break-words text-4xl">{node.name}</h2>
        {node.module && <p className="mt-1 break-all font-mono text-xs text-muted">{node.module}</p>}
        {node.responsibility && <p className="mt-3 max-w-prose">{node.responsibility}</p>}
      </header>

      <section aria-labelledby="conf-h" className="rounded-xl border border-line bg-surface-2 p-4">
        <h3 id="conf-h" className="eyebrow mb-2">Confidence</h3>
        <ConfidenceMeter value={node.confidence} />
        <p className="mt-2 text-sm text-muted">{CONFIDENCE_WHY[node.confidence]} Backed by {node.evidence.length} quote{node.evidence.length === 1 ? "" : "s"}.</p>
      </section>

      <section aria-labelledby="ev-h">
        <h3 id="ev-h" className="eyebrow mb-2">Evidence</h3>
        {node.evidence.length === 0 ? <Notice tone="warn">No quote is attached to this node.</Notice> : <div className="space-y-3">{node.evidence.map((ev, i) => <EvidenceQuote key={i} ev={ev} />)}</div>}
      </section>

      {(out.length > 0 || inn.length > 0) && (
        <section aria-labelledby="rel-h" className="space-y-3">
          <h3 id="rel-h" className="eyebrow">Relationships ({edges.length})</h3>
          {out.length > 0 && <div><p className="mb-1 text-xs text-muted">Outgoing</p>{rel(out, "out")}</div>}
          {inn.length > 0 && <div><p className="mb-1 text-xs text-muted">Incoming</p>{rel(inn, "in")}</div>}
        </section>
      )}

      {related.length > 0 && (
        <section aria-labelledby="fi-h">
          <h3 id="fi-h" className="eyebrow mb-2">Findings that mention this node</h3>
          <ul className="space-y-2">
            {related.map((f) => (
              <li key={f.id}><Link href={`/projects/${projectId}/findings#${f.id}`} className="flex items-center gap-2 rounded-lg border border-line p-2.5 text-sm hover:bg-surface-2"><SeverityTag severity={f.severity} />{f.title}</Link></li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="cm-h">
        <h3 id="cm-h" className="eyebrow mb-2">Comments</h3>
        {comments.data?.items.length ? (
          <ul className="mb-3 space-y-2">
            {comments.data.items.map((c) => <li key={c.id} className="rounded-lg border border-line p-3 text-sm"><p>{c.text}</p><p className="mt-1 text-xs text-muted">{c.author ?? "someone"} · {relTime(c.created_at)}</p></li>)}
          </ul>
        ) : <p className="mb-3 text-sm text-muted">{comments.loading ? "Loading comments" : "No comments on this node yet."}</p>}
        <form onSubmit={addComment} className="space-y-2">
          <label htmlFor="cmt" className="sr-only">Add a comment on {node.name}</label>
          <Textarea id="cmt" value={text} onChange={(e) => setText(e.target.value)} maxLength={5000} placeholder="Add context for reviewers" />
          <Button type="submit" loading={busy} disabled={!text.trim()}>Comment</Button>
        </form>
      </section>
    </article>
  );
}
