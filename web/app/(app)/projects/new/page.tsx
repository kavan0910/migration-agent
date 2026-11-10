"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/hooks";
import { validateArchive, validateGitUrl } from "@/lib/validate";
import { Button, Field, Input, Notice, PageHeader, cx } from "@/components/ui";

export default function NewProject() {
  const { workspace } = useAuth();
  const router = useRouter();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"git" | "upload">("git");
  const [git, setGit] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [errors, setErrors] = useState<{ name?: string; source?: string }>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function pick(f: File | undefined) {
    if (!f) return;
    setFile(f);
    setErrors((e) => ({ ...e, source: validateArchive(f) ?? undefined }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const fe: typeof errors = {};
    if (name.trim().length < 2) fe.name = "Give the project a name (2 to 80 characters).";
    if (mode === "git") fe.source = validateGitUrl(git) ?? undefined;
    else fe.source = file ? validateArchive(file) ?? undefined : "Choose an archive to upload.";
    setErrors(fe);
    if (fe.name || fe.source || !workspace) return;
    setBusy(true);
    setServerError(null);
    try {
      let pid = created;
      if (!pid) {
        pid = (await api.projects.create(workspace.id, name.trim())).id;
        setCreated(pid);
      }
      if (mode === "git") await api.projects.setGitSource(pid, git.trim());
      else await api.projects.uploadSource(pid, file!);
      router.push(`/projects/${pid}`);
    } catch (err) {
      setServerError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <>
      <title>New project · Migration Agent</title>
      <nav aria-label="Breadcrumb" className="mb-4 text-sm text-muted"><Link href="/dashboard" className="hover:underline">Projects</Link> / <span className="text-ink">New</span></nav>
      <PageHeader eyebrow="Step 1 of 2" title="Connect a service">
        Point us at the code. Nothing runs until you have seen the estimated cost on the next screen.
      </PageHeader>
      <form onSubmit={submit} noValidate className="rise rise-1 max-w-2xl space-y-7">
        <Field label="Project name" htmlFor="pname" error={errors.name}>
          <Input id="pname" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="orders-service" aria-invalid={!!errors.name} disabled={!!created} />
        </Field>

        <fieldset>
          <legend className="mb-2 text-sm font-medium">Source</legend>
          <div role="radiogroup" aria-label="Source type" className="inline-flex rounded-lg border border-line-strong bg-surface p-1">
            {(["git", "upload"] as const).map((m) => (
              <label key={m} className={cx("cursor-pointer rounded-md px-4 py-2 text-sm font-medium has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent", mode === m ? "bg-ink text-bg" : "text-muted hover:text-ink")}>
                <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => { setMode(m); setErrors((x) => ({ ...x, source: undefined })); }} className="sr-only" />
                {m === "git" ? "Git URL" : "Upload archive"}
              </label>
            ))}
          </div>

          <div className="mt-4">
            {mode === "git" ? (
              <Field label="Repository URL" htmlFor="git" error={errors.source} hint="Public https URLs only. Private, loopback and link-local hosts are rejected.">
                <Input id="git" type="url" inputMode="url" value={git} onChange={(e) => setGit(e.target.value)} placeholder="https://github.com/org/repo" aria-invalid={!!errors.source} className="font-mono" />
              </Field>
            ) : (
              <Field label="Archive" htmlFor="file" error={errors.source} hint=".zip, .tar.gz or .tgz, up to 200 MB. Secrets are redacted at ingest.">
                <div
                  onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
                  onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
                  className={cx("dotgrid flex flex-col items-center gap-2 rounded-xl border border-dashed px-4 py-9 text-center", drag ? "border-accent bg-accent-soft" : "border-line-strong")}
                >
                  <p className="text-sm">{file ? <><span className="font-mono">{file.name}</span> <span className="text-muted">({(file.size / 1048576).toFixed(1)} MB)</span></> : "Drop an archive here or"}</p>
                  <Button onClick={() => input.current?.click()}>{file ? "Choose a different file" : "Choose file"}</Button>
                  <input ref={input} id="file" type="file" accept=".zip,.tar.gz,.tgz,application/zip,application/gzip" className="sr-only" onChange={(e) => pick(e.target.files?.[0])} />
                </div>
              </Field>
            )}
          </div>
        </fieldset>

        {created && <Notice tone="info">Project created. Fix the source and submit again to continue.</Notice>}
        {serverError && <Notice tone="danger" title="Could not add the source">{serverError}</Notice>}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="primary" loading={busy}>Create project</Button>
          <Link href="/dashboard" className="inline-flex min-h-10 items-center rounded-lg px-4 text-sm hover:bg-surface-2">Cancel</Link>
        </div>
      </form>
    </>
  );
}
