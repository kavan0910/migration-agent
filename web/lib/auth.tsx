"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { tokenStore } from "./token";
import type { User, Workspace } from "./types";

type Status = "loading" | "authed" | "anon";
interface AuthValue {
  status: Status;
  user: User | null;
  workspaces: Workspace[];
  workspace: Workspace | null;
  selectWorkspace: (id: string) => void;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => void;
  refreshWorkspaces: () => Promise<void>;
}
const Ctx = createContext<AuthValue | null>(null);
const WS_KEY = "ma-workspace";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [wid, setWid] = useState<string | null>(null);

  const loadSession = useCallback(async () => {
    const [me, ws] = await Promise.all([api.auth.me(), api.workspaces.list()]);
    setUser(me);
    setWorkspaces(ws.items);
    let saved: string | null = null;
    try { saved = localStorage.getItem(WS_KEY); } catch { /* ignore */ }
    setWid((cur) => cur ?? (ws.items.find((w) => w.id === saved) ?? ws.items[0])?.id ?? null);
    setStatus("authed");
  }, []);

  useEffect(() => {
    // Cookie mode has no readable token, so always probe /v1/me. Bearer mode probes only when a token exists.
    if (!tokenStore.get() && process.env.NEXT_PUBLIC_AUTH_COOKIE !== "1") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStatus("anon");
      return;
    }
    loadSession().catch(() => { tokenStore.set(null); setStatus("anon"); });
  }, [loadSession]);

  useEffect(() => tokenStore.subscribe((t) => { if (!t && process.env.NEXT_PUBLIC_AUTH_COOKIE !== "1") { setUser(null); setStatus("anon"); } }), []);

  const finish = useCallback(async (token: string | undefined) => {
    if (token) tokenStore.set(token);
    await loadSession();
  }, [loadSession]);

  const value = useMemo<AuthValue>(() => ({
    status, user, workspaces,
    workspace: workspaces.find((w) => w.id === wid) ?? null,
    selectWorkspace: (id) => { setWid(id); try { localStorage.setItem(WS_KEY, id); } catch { /* ignore */ } },
    login: async (email, password) => { const r = await api.auth.login(email, password); await finish(r.access_token); },
    signup: async (email, password, name) => { const r = await api.auth.signup(email, password, name); await finish(r.access_token); },
    logout: () => { tokenStore.set(null); setUser(null); setWorkspaces([]); setWid(null); setStatus("anon"); },
    refreshWorkspaces: async () => { const ws = await api.workspaces.list(); setWorkspaces(ws.items); },
  }), [status, user, workspaces, wid, finish]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}
