"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage, useQuery } from "@/lib/hooks";
import { dateTime } from "@/lib/format";
import type { Member, Role } from "@/lib/types";
import { Button, Card, Dialog, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, PageHeader, Select } from "@/components/ui";
import { useToast } from "@/components/Toast";

const ROLES: Array<{ id: Role; blurb: string }> = [
  { id: "owner", blurb: "Everything, including members and deletion" },
  { id: "admin", blurb: "Manage members, projects and audit log" },
  { id: "member", blurb: "Create projects, run analyses, review" },
  { id: "viewer", blurb: "Read only" },
];

export default function MembersPage() {
  const { workspace, user } = useAuth();
  const wid = workspace?.id;
  const toast = useToast();
  const members = useQuery(() => api.workspaces.members(wid!), [wid], !!wid);
  const canManage = workspace?.role === "owner" || workspace?.role === "admin";
  const audit = useQuery(() => api.workspaces.audit(wid!), [wid, canManage], !!wid && canManage);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.workspaces.addMember(wid!, email.trim(), role); setEmail(""); members.reload(); audit.reload(); toast("Member added"); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function remove() {
    if (!removing) return;
    setRemoveBusy(true);
    try { await api.workspaces.removeMember(wid!, removing.user_id); toast(`Removed ${removing.email}`); setRemoving(null); members.reload(); audit.reload(); } catch (err) { toast(errorMessage(err), "error"); } finally { setRemoveBusy(false); }
  }

  return (
    <>
      <title>Members · Migration Agent</title>
      <PageHeader eyebrow={workspace?.name} title="Members">People who can see and work on this workspace&apos;s projects.</PageHeader>
      {members.loading && !members.data ? <LoadingBlock label="Loading members" rows={4} /> : members.error ? <ErrorState error={members.error} onRetry={members.reload} /> : members.data?.items.length === 0 ? <EmptyState title="No members" /> : (
        <Card className="rise rise-1 mb-10 overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-sm">
            <caption className="sr-only">Workspace members</caption>
            <thead className="eyebrow border-b border-line"><tr><th scope="col" className="px-4 py-3 font-normal">Person</th><th scope="col" className="px-4 py-3 font-normal">Role</th><th scope="col" className="px-4 py-3 font-normal">Joined</th><th scope="col" className="px-4 py-3"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {members.data?.items.map((m) => (
                <tr key={m.user_id} className="border-b border-line last:border-0">
                  <td className="px-4 py-3"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-full bg-accent-soft font-mono text-xs font-semibold text-accent" aria-hidden="true">{(m.name ?? m.email).slice(0, 2).toUpperCase()}</span><div><p className="font-medium">{m.name ?? m.email}{m.user_id === user?.id && <span className="ml-2 text-xs text-muted">(you)</span>}</p><p className="text-xs text-muted">{m.email}</p></div></div></td>
                  <td className="px-4 py-3"><span className="rounded-full bg-surface-2 px-2.5 py-0.5 text-xs font-medium capitalize">{m.role}</span></td>
                  <td className="px-4 py-3 text-muted">{m.created_at ? dateTime(m.created_at) : "n/a"}</td>
                  <td className="px-4 py-3 text-right">{canManage && m.user_id !== user?.id && <Button variant="ghost" onClick={() => setRemoving(m)} aria-label={`Remove ${m.email}`}>Remove</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {canManage ? (
        <Card className="mb-10 max-w-2xl p-6">
          <h2 className="display text-3xl">Add a member</h2>
          <form onSubmit={add} className="mt-4 grid gap-4 sm:grid-cols-[1fr_180px_auto] sm:items-end">
            <Field label="Email" htmlFor="m-email"><Input id="m-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="teammate@company.com" /></Field>
            <Field label="Role" htmlFor="m-role"><Select id="m-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>{ROLES.map((r) => <option key={r.id} value={r.id}>{r.id}</option>)}</Select></Field>
            <Button type="submit" variant="primary" loading={busy}>Add</Button>
          </form>
          <p className="mt-2 text-xs text-muted">{ROLES.find((r) => r.id === role)?.blurb}.</p>
          {error && <div className="mt-3"><Notice tone="danger">{error}</Notice></div>}
        </Card>
      ) : <div className="mb-10 max-w-2xl"><Notice tone="info">Only owners and admins can change membership.</Notice></div>}

      {canManage && (
        <section aria-labelledby="audit-h">
          <h2 id="audit-h" className="display mb-4 text-3xl">Audit log</h2>
          {audit.loading && !audit.data ? <LoadingBlock label="Loading audit log" rows={2} /> : audit.error ? <ErrorState error={audit.error} onRetry={audit.reload} /> : (
            <Card><ul className="divide-y divide-line">{audit.data?.items.map((a) => <li key={a.id} className="flex flex-wrap items-baseline gap-x-4 px-4 py-3 text-sm"><span className="font-mono text-xs text-muted">{dateTime(a.at)}</span><span className="font-mono">{a.action}</span><span>{a.target}</span><span className="ml-auto text-muted">{a.actor}</span></li>)}</ul></Card>
          )}
        </section>
      )}

      <Dialog open={!!removing} onClose={() => setRemoving(null)} title="Remove member?" footer={<><Button onClick={() => setRemoving(null)}>Cancel</Button><Button variant="danger" onClick={remove} loading={removeBusy}>Remove</Button></>}>
        <p className="text-sm"><strong>{removing?.email}</strong> will lose access to every project in this workspace immediately.</p>
      </Dialog>
    </>
  );
}
