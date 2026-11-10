"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { Logo, ThemeToggle } from "./Brand";
import { Button, LoadingBlock, Select, cx } from "./ui";
import { API_MOCK } from "@/lib/api";

const NAV = [
  { href: "/dashboard", label: "Projects", icon: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" },
  { href: "/usage", label: "Usage and plan", icon: "M4 19V9m6 10V5m6 14v-7m4 7H2" },
  { href: "/members", label: "Members", icon: "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM21 19v-1a4 4 0 0 0-3-3.9M16 4.1a3 3 0 0 1 0 5.8" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { status, user, workspaces, workspace, selectWorkspace, logout } = useAuth();
  const router = useRouter();
  const path = usePathname();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (status === "anon") router.replace("/login");
  }, [status, router]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOpen(false);
  }, [path]);

  if (status !== "authed") {
    return <div className="mx-auto max-w-3xl p-10"><LoadingBlock label="Checking your session" rows={4} /></div>;
  }

  const nav = (
    <nav aria-label="Workspace" className="flex flex-col gap-1">
      {NAV.map((n) => {
        const active = path === n.href || (n.href === "/dashboard" && path.startsWith("/projects"));
        return (
          <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
            className={cx("flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium", active ? "bg-ink text-bg" : "hover:bg-surface-2")}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={n.icon} /></svg>
            {n.label}
          </Link>
        );
      })}
    </nav>
  );

  const account = (
    <div className="space-y-3 border-t border-line pt-4">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0"><p className="truncate text-sm font-medium">{user?.name ?? user?.email}</p><p className="truncate text-xs text-muted">{user?.email}</p></div>
        <ThemeToggle />
      </div>
      <Button className="w-full" onClick={() => { logout(); router.replace("/"); }}>Sign out</Button>
    </div>
  );

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[268px_1fr]">
      <aside className="sticky top-0 hidden h-dvh flex-col gap-6 border-r border-line bg-surface p-5 lg:flex">
        <Logo href="/dashboard" />
        <div>
          <label htmlFor="ws" className="eyebrow mb-1.5 block">Workspace</label>
          <Select id="ws" value={workspace?.id ?? ""} onChange={(e) => selectWorkspace(e.target.value)}>
            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </Select>
        </div>
        {nav}
        <div className="mt-auto space-y-3">
          {API_MOCK && <p className="rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">Demo mode: sample data, no backend.</p>}
          {account}
        </div>
      </aside>

      <div className="min-w-0">
        <div className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-line bg-bg/90 px-4 backdrop-blur lg:hidden">
          <Logo href="/dashboard" />
          <button type="button" aria-expanded={open} aria-controls="mobile-menu" onClick={() => setOpen((o) => !o)} className="grid size-10 place-items-center rounded-lg hover:bg-surface-2">
            <span className="sr-only">{open ? "Close menu" : "Open menu"}</span>
            <svg width="20" height="20" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" fill="none" aria-hidden="true">{open ? <path d="M6 6l12 12M18 6 6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}</svg>
          </button>
        </div>
        {open && (
          <div id="mobile-menu" className="space-y-5 border-b border-line bg-surface p-4 lg:hidden">
            <Select aria-label="Workspace" value={workspace?.id ?? ""} onChange={(e) => selectWorkspace(e.target.value)}>
              {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </Select>
            {nav}
            {account}
          </div>
        )}
        <main id="main" className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-8 lg:py-12">{children}</main>
      </div>
    </div>
  );
}
