"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/hooks";
import { Button, Card, Dialog, Field, Input, Notice } from "@/components/ui";
import { useProject } from "@/components/ProjectContext";
import { useToast } from "@/components/Toast";

export default function SettingsPage() {
  const { project, setProject } = useProject();
  const { workspace } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [name, setName] = useState(project.name);
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canAdmin = workspace?.role === "owner" || workspace?.role === "admin";

  async function rename(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try { setProject(await api.projects.rename(project.id, name.trim())); toast("Project renamed"); } catch (err) { toast(errorMessage(err), "error"); } finally { setSaving(false); }
  }
  async function remove() {
    setDeleting(true);
    setError(null);
    try {
      await api.projects.remove(project.id);
      toast(`Deleted ${project.name} and all of its data`);
      router.replace("/dashboard");
    } catch (err) {
      setError(errorMessage(err));
      setDeleting(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-8">
      <Card className="p-6">
        <h2 className="display text-3xl">General</h2>
        <form onSubmit={rename} className="mt-4 space-y-4">
          <Field label="Project name" htmlFor="rename"><Input id="rename" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} disabled={workspace?.role === "viewer"} /></Field>
          <Button type="submit" loading={saving} disabled={name.trim().length < 2 || name.trim() === project.name || workspace?.role === "viewer"}>Save name</Button>
        </form>
      </Card>

      <Card className="border-danger/50 p-6">
        <h2 className="display text-3xl text-danger">Delete project and all data</h2>
        <p className="mt-2 text-muted">
          This permanently removes the source copy, the knowledge graph, every run and artifact, findings, comments and cached model responses for this project.
          An audit record of the deletion is kept. This cannot be undone.
        </p>
        {!canAdmin && <div className="mt-3"><Notice tone="warn">Only workspace owners and admins can delete projects.</Notice></div>}
        <Button variant="danger" className="mt-4" disabled={!canAdmin} onClick={() => { setTyped(""); setError(null); setOpen(true); }}>Delete this project</Button>
      </Card>

      <Dialog
        open={open} onClose={() => !deleting && setOpen(false)} title="Delete this project?"
        footer={<><Button onClick={() => setOpen(false)} disabled={deleting}>Cancel</Button><Button variant="danger" onClick={remove} loading={deleting} disabled={typed !== project.name}>Delete forever</Button></>}
      >
        <p className="text-sm">All data for <strong>{project.name}</strong> will be erased. To confirm, type the project name exactly:</p>
        <p className="mt-2 select-all rounded bg-surface-2 px-2 py-1 font-mono text-sm">{project.name}</p>
        <div className="mt-3"><Field label="Project name" htmlFor="confirm-name"><Input id="confirm-name" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} aria-describedby="confirm-hint" /></Field></div>
        <p id="confirm-hint" className="sr-only">The delete button enables when the text matches the project name.</p>
        {error && <div className="mt-3"><Notice tone="danger">{error}</Notice></div>}
      </Dialog>
    </div>
  );
}
