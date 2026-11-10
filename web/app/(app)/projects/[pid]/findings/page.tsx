"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { errorMessage, useQuery } from "@/lib/hooks";
import { relTime } from "@/lib/format";
import type { Finding, ResolutionStatus } from "@/lib/types";
import { Button, Card, EmptyState, ErrorState, LoadingBlock, Notice, SeverityTag, Textarea, cx } from "@/components/ui";
import { useProject } from "@/components/ProjectContext";
import { useToast } from "@/components/Toast";

const ANSWERS: Array<{ id: ResolutionStatus; label: string; hint: string }> = [
  { id: "accepted_doc", label: "The document is right", hint: "Queues a re-analysis" },
  { id: "accepted_code", label: "The code is right", hint: "Records that the doc text needs fixing" },
  { id: "dismissed", label: "Not a real problem", hint: "Closes the finding" },
  { id: "needs_reanalysis", label: "Look again", hint: "Queues a re-analysis" },
];
const LABEL: Record<string, string> = Object.fromEntries(ANSWERS.map((a) => [a.id, a.label]));

function FindingCard({ f, projectId, onChanged }: { f: Finding; projectId: string; onChanged: (f: Finding) => void }) {
  const toast = useToast();
  const [status, setStatus] = useState<ResolutionStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const comments = useQuery(() => api.review.comments(projectId, f.id), [projectId, f.id]);
  const res = f.resolution && f.resolution.status !== "open" ? f.resolution : null;

  async function resolve(e: React.FormEvent) {
    e.preventDefault();
    if (!status) return;
    setBusy(true);
    try {
      const out = await api.review.resolve(projectId, f.id, status, note);
      onChanged({ ...f, resolution: out.resolution });
      setStatus(null); setNote("");
      toast(status === "needs_reanalysis" || status === "accepted_doc" ? "Saved. A re-analysis run was queued; follow it on the Overview tab." : "Saved");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }
  async function comment(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    try { await api.review.comment(projectId, f.id, text.trim()); setText(""); comments.reload(); } catch (err) { toast(errorMessage(err), "error"); }
  }

  return (
    <Card id={f.id} className="scroll-mt-24 p-5 sm:p-6 target:border-accent">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="min-w-0 flex-1 text-lg font-semibold leading-snug">{f.title}</h2>
        <div className="flex items-center gap-2"><SeverityTag severity={f.severity} />{res && <span className="rounded-full bg-ok-soft px-2.5 py-0.5 text-xs font-medium text-ok">{LABEL[res.status] ?? res.status}</span>}</div>
      </div>
      {f.detail && <p className="mt-2 max-w-prose text-muted">{f.detail}</p>}

      {f.quotes.length > 0 && (
        <div className={cx("mt-4 grid gap-3", f.quotes.length > 1 && "md:grid-cols-2")}>
          {f.quotes.slice(0, 4).map((q, i) => (
            <figure key={i} className="quote overflow-hidden">
              <figcaption className="break-all border-b border-line px-3 py-1.5 font-mono text-xs text-accent">{q.source}</figcaption>
              <pre className="m-0 px-3 py-2">{q.text}</pre>
            </figure>
          ))}
        </div>
      )}
      {f.item_ids.length > 0 && (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted">Related:
          {f.item_ids.map((id) => <Link key={id} href={`/projects/${projectId}/graph?node=${id}`} className="rounded bg-accent-soft px-1.5 py-0.5 font-mono text-xs text-accent hover:underline">{id}</Link>)}
        </p>
      )}

      {res && (
        <div className="mt-4"><Notice tone="ok" title={`Resolved: ${LABEL[res.status] ?? res.status}`}>{res.note || "No note."}<span className="mt-1 block text-xs text-muted">{res.author ?? "someone"} · {relTime(res.created_at ?? res.at)}</span></Notice></div>
      )}

      <form onSubmit={resolve} className="mt-5 space-y-3 border-t border-line pt-4">
        <fieldset>
          <legend className="mb-2 text-sm font-medium">{res ? "Change the answer" : "Your answer"}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {ANSWERS.map((a) => (
              <label key={a.id} className={cx("cursor-pointer rounded-lg border p-3 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent", status === a.id ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-surface-2")}>
                <input type="radio" name={`ans-${f.id}`} value={a.id} checked={status === a.id} onChange={() => setStatus(a.id)} className="sr-only" />
                <span className="block text-sm font-medium">{a.label}</span><span className="block text-xs text-muted">{a.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <label htmlFor={`note-${f.id}`} className="mb-1 block text-sm font-medium">Note (optional)</label>
          <Textarea id={`note-${f.id}`} value={note} onChange={(e) => setNote(e.target.value)} maxLength={5000} placeholder="Why? This is recorded with your name." className="min-h-16" />
        </div>
        <Button type="submit" variant="primary" disabled={!status} loading={busy}>Save answer</Button>
      </form>

      <details className="mt-5">
        <summary className="cursor-pointer text-sm font-medium">Discussion ({comments.data?.total ?? 0})</summary>
        <ul className="mt-3 space-y-2">
          {comments.data?.items.map((c) => <li key={c.id} className="rounded-lg bg-surface-2 p-3 text-sm"><p>{c.text}</p><p className="mt-1 text-xs text-muted">{c.author} · {relTime(c.created_at)}</p></li>)}
        </ul>
        <form onSubmit={comment} className="mt-3 space-y-2">
          <label htmlFor={`c-${f.id}`} className="sr-only">Comment on {f.title}</label>
          <Textarea id={`c-${f.id}`} value={text} onChange={(e) => setText(e.target.value)} maxLength={5000} placeholder="Comment for your team" className="min-h-16" />
          <Button type="submit" disabled={!text.trim()}>Comment</Button>
        </form>
      </details>
    </Card>
  );
}

export default function FindingsPage() {
  const { project, reload } = useProject();
  const q = useQuery(() => api.review.findings(project.id), [project.id]);
  const [show, setShow] = useState<"open" | "all">("open");

  if (q.loading && !q.data) return <LoadingBlock label="Loading findings" rows={3} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} title="Could not load findings" />;
  const all = q.data?.items ?? [];
  if (all.length === 0) {
    return <EmptyState title="Nothing to check">Run a verify pass with your own requirements documents and contradictions appear here for review.</EmptyState>;
  }
  const isOpen = (f: Finding) => !f.resolution || f.resolution.status === "open";
  const order = { high: 0, medium: 1, low: 2 };
  const list = all.filter((f) => show === "all" || isOpen(f)).sort((a, b) => order[a.severity] - order[b.severity]);
  const open = all.filter(isOpen).length;

  return (
    <div className="max-w-4xl">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <p className="text-muted"><span className="font-semibold text-ink tnum">{open}</span> of {all.length} open</p>
        <div role="group" aria-label="Show" className="inline-flex rounded-lg border border-line-strong bg-surface p-1">
          {(["open", "all"] as const).map((s) => <button key={s} type="button" aria-pressed={show === s} onClick={() => setShow(s)} className={cx("rounded-md px-3 py-1.5 text-sm font-medium", show === s ? "bg-ink text-bg" : "text-muted hover:text-ink")}>{s === "open" ? "Open" : "All"}</button>)}
        </div>
      </div>
      {list.length === 0 ? <EmptyState title="All done">You answered every finding. Start a re-analysis from the Overview tab to refresh the document.</EmptyState> : (
        <div className="space-y-5">
          {list.map((f) => <FindingCard key={f.id} f={f} projectId={project.id} onChanged={(u) => { q.setData((d) => d && { ...d, items: d.items.map((x) => (x.id === u.id ? u : x)) }); reload(); }} />)}
        </div>
      )}
    </div>
  );
}
