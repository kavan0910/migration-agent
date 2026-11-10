"use client";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { errorMessage, useQuery } from "@/lib/hooks";
import { extractHeadings } from "@/lib/markdown";
import { Button, EmptyState, ErrorState, LinkButton, LoadingBlock } from "@/components/ui";
import { Markdown } from "@/components/Markdown";
import { useProject } from "@/components/ProjectContext";
import { useToast } from "@/components/Toast";

export default function DocumentPage() {
  const { project } = useProject();
  const toast = useToast();
  const doc = useQuery(() => api.results.document(project.id), [project.id]);
  const [busy, setBusy] = useState<string | null>(null);
  const headings = useMemo(() => extractHeadings(doc.data?.markdown ?? "").filter((h) => h.level === 2), [doc.data]);

  async function download(format: "md" | "json" | "html") {
    setBusy(format);
    try {
      const blob = await api.results.exportBlob(project.id, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${project.name.replace(/[^a-z0-9._-]+/gi, "-")}-understanding.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(null);
    }
  }

  if (doc.loading && !doc.data) return <LoadingBlock label="Loading document" rows={5} />;
  if (doc.error) return <ErrorState error={doc.error} onRetry={doc.reload} title="Could not load the document" />;
  if (!doc.data?.markdown) {
    return <EmptyState title="No document yet" action={<LinkButton href={`/projects/${project.id}`} variant="primary">Go to runs</LinkButton>}>Run an analysis and the understanding document appears here, with every claim linked to its evidence.</EmptyState>;
  }
  return (
    <div className="grid gap-10 lg:grid-cols-[220px_1fr]">
      <aside className="lg:sticky lg:top-6 lg:self-start">
        <nav aria-label="Document contents">
          <p className="eyebrow mb-2">Contents</p>
          <ol className="space-y-0.5 text-sm">
            {headings.map((h) => <li key={h.id}><a href={`#${h.id}`} className="block rounded px-2 py-1 text-muted hover:bg-surface-2 hover:text-ink">{h.text.replace(/^\d+\.\s*/, "")}</a></li>)}
          </ol>
        </nav>
        <div className="mt-6 flex flex-wrap gap-2 lg:flex-col" role="group" aria-label="Export">
          {(["md", "json", "html"] as const).map((f) => <Button key={f} onClick={() => download(f)} loading={busy === f} className="lg:w-full">Export .{f}</Button>)}
        </div>
        {doc.data.version != null && <p className="mt-4 font-mono text-xs text-muted">graph v{doc.data.version}</p>}
      </aside>
      <article className="min-w-0"><Markdown source={doc.data.markdown} projectId={project.id} /></article>
    </div>
  );
}
