/**
 * The only module that talks to the backend (docs/platform-contract.md section 3).
 * Real mode: fetch against NEXT_PUBLIC_API_URL. Mock mode (NEXT_PUBLIC_API_MOCK=1): in-browser fixtures.
 */
import { SseParser } from "./sse";
import { tokenStore } from "./token";
import type {
  ApiErrorBody, AuditEntry, Comment, DocumentResponse, Entitlements, Estimate, EstimateRequest, Finding,
  GraphDiff, GraphEdge, GraphNode, GraphPage, GraphQuery, GraphVersion, ListResponse, Member, NodeDetail,
  Project, ResolutionStatus, ResolveResult, Role, Run, RunEvent, RunRequest, TokenResponse, Usage, User, Workspace,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
export const API_MOCK = process.env.NEXT_PUBLIC_API_MOCK === "1";
const COOKIE_MODE = process.env.NEXT_PUBLIC_AUTH_COOKIE === "1";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export interface MockRequest {
  method: string;
  path: string;
  query: Record<string, string>;
  body: unknown;
  token: string | null;
}
export interface MockResponse {
  status: number;
  body: unknown;
}

type Query = Record<string, string | number | boolean | null | undefined>;
interface Opts {
  body?: unknown;
  form?: FormData;
  query?: Query;
  signal?: AbortSignal;
}

function buildQuery(q?: Query): string {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function parseErrorBody(status: number, text: string): ApiError {
  try {
    const j = JSON.parse(text) as Partial<ApiErrorBody>;
    if (j.error && typeof j.error.message === "string") {
      return new ApiError(status, String(j.error.code ?? "error"), j.error.message);
    }
  } catch {
    /* not JSON */
  }
  return new ApiError(status, "http_error", `Request failed with status ${status}`);
}

async function request<T>(method: string, path: string, opts: Opts = {}): Promise<T> {
  const token = tokenStore.get();
  if (API_MOCK) {
    const { handleMock } = await import("./mock/server");
    const q: Record<string, string> = {};
    for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null && v !== "") q[k] = String(v);
    const res = await handleMock({ method, path, query: q, body: opts.form ?? opts.body, token });
    if (res.status >= 400) {
      const e = (res.body as ApiErrorBody).error;
      throw new ApiError(res.status, e.code, e.message);
    }
    return res.body as T;
  }
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token && !COOKIE_MODE) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}${buildQuery(opts.query)}`, {
      method, headers, body, signal: opts.signal, credentials: COOKIE_MODE ? "include" : "omit",
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again.");
  }
  const text = await res.text();
  if (!res.ok) throw parseErrorBody(res.status, text);
  if (!text) return undefined as T;
  // Document/changelog endpoints answer text/markdown; everything else is JSON.
  if (!(res.headers.get("content-type") ?? "").includes("json")) return text as unknown as T;
  return JSON.parse(text) as T;
}

/** The backend names the display name `display_name`; the UI uses `name`. */
function normUser<T extends { display_name?: string | null; name?: string | null }>(u: T): T {
  return u && u.name == null && u.display_name != null ? { ...u, name: u.display_name } : u;
}
function normToken(t: TokenResponse): TokenResponse {
  return t.user ? { ...t, user: normUser(t.user) } : t;
}
const asDoc = (raw: unknown): DocumentResponse =>
  typeof raw === "string" ? { markdown: raw } : (raw as DocumentResponse);

const enc = encodeURIComponent;
const get = <T,>(p: string, query?: Query, signal?: AbortSignal) => request<T>("GET", p, { query, signal });
const post = <T,>(p: string, body?: unknown) => request<T>("POST", p, { body });

/** Accepts either `{nodes}` (engine snapshot shape) or `{items}` (list envelope). */
export function normaliseGraphPage(raw: Record<string, unknown>): GraphPage {
  const nodes = (raw.nodes ?? raw.items ?? []) as GraphNode[];
  return {
    version: Number(raw.version ?? 0),
    total: Number(raw.total ?? nodes.length),
    nodes,
    edges: (raw.edges ?? []) as GraphEdge[],
  };
}

export const api = {
  health: () => get<{ status: string }>("/healthz"),
  auth: {
    signup: (email: string, password: string, name?: string) =>
      post<TokenResponse>("/v1/auth/signup", { email, password, name, display_name: name }).then(normToken),
    login: (email: string, password: string) =>
      post<TokenResponse>("/v1/auth/login", { email, password }).then(normToken),
    me: (signal?: AbortSignal) => get<User>("/v1/me", undefined, signal).then(normUser),
  },
  workspaces: {
    list: () => get<ListResponse<Workspace>>("/v1/workspaces"),
    get: (wid: string) => get<Workspace>(`/v1/workspaces/${enc(wid)}`),
    create: (name: string) => post<Workspace>("/v1/workspaces", { name }),
    members: (wid: string) =>
      get<ListResponse<Member>>(`/v1/workspaces/${enc(wid)}/members`).then((r) => ({ ...r, items: r.items.map(normUser) })),
    addMember: (wid: string, email: string, role: Role) =>
      post<Member>(`/v1/workspaces/${enc(wid)}/members`, { email, role }).then(normUser),
    removeMember: (wid: string, uid: string) =>
      request<void>("DELETE", `/v1/workspaces/${enc(wid)}/members/${enc(uid)}`),
    usage: (wid: string, range?: { from?: string; to?: string }) =>
      get<Usage & { by_run?: Usage["runs"] }>(`/v1/workspaces/${enc(wid)}/usage`, range).then((u) => ({
        ...u,
        totals: { ...u.totals, runs: u.totals.runs ?? u.by_run?.length ?? 0 },
        runs: u.runs ?? u.by_run ?? [],
      })),
    entitlements: (wid: string) => get<Entitlements>(`/v1/workspaces/${enc(wid)}/entitlements`),
    audit: (wid: string) => get<ListResponse<AuditEntry>>(`/v1/workspaces/${enc(wid)}/audit`),
  },
  projects: {
    list: (wid: string, page: { limit?: number; offset?: number } = {}) =>
      get<ListResponse<Project>>(`/v1/workspaces/${enc(wid)}/projects`, page),
    create: (wid: string, name: string) => post<Project>(`/v1/workspaces/${enc(wid)}/projects`, { name }),
    get: (pid: string) => get<Project>(`/v1/projects/${enc(pid)}`),
    rename: (pid: string, name: string) => request<Project>("PATCH", `/v1/projects/${enc(pid)}`, { body: { name } }),
    remove: (pid: string) => request<void>("DELETE", `/v1/projects/${enc(pid)}`),
    setGitSource: (pid: string, gitUrl: string) =>
      post<unknown>(`/v1/projects/${enc(pid)}/source`, { git_url: gitUrl }),
    uploadSource: (pid: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<unknown>("POST", `/v1/projects/${enc(pid)}/source`, { form });
    },
    estimate: (pid: string, req: EstimateRequest = {}) => post<Estimate>(`/v1/projects/${enc(pid)}/estimate`, req),
  },
  runs: {
    list: (pid: string, page: { limit?: number; offset?: number } = {}) =>
      get<ListResponse<Run>>(`/v1/projects/${enc(pid)}/runs`, page),
    start: (pid: string, req: RunRequest) => post<Run>(`/v1/projects/${enc(pid)}/runs`, req),
    get: (rid: string) => get<Run>(`/v1/runs/${enc(rid)}`),
    cancel: (rid: string) => post<Run>(`/v1/runs/${enc(rid)}/cancel`),
    /**
     * Streams run events. EventSource cannot send an Authorization header, so this uses fetch + a
     * streaming parser (same wire format). Resolves when the stream ends; abort with the signal.
     */
    stream: (rid: string, onEvent: (e: RunEvent) => void, signal?: AbortSignal) => streamRun(rid, onEvent, signal),
  },
  graph: {
    page: async (pid: string, q: GraphQuery = {}, signal?: AbortSignal) =>
      normaliseGraphPage(await get<Record<string, unknown>>(`/v1/projects/${enc(pid)}/graph`, { ...q }, signal)),
    node: (pid: string, nid: string) =>
      get<NodeDetail>(`/v1/projects/${enc(pid)}/graph/nodes/${enc(nid)}`).then((d) => ({ ...d, neighbours: d.neighbours ?? [] })),
    versions: (pid: string) => get<ListResponse<GraphVersion>>(`/v1/projects/${enc(pid)}/graph/versions`),
    diff: (pid: string, a: number, b: number) => get<GraphDiff>(`/v1/projects/${enc(pid)}/graph/diff`, { a, b }),
  },
  results: {
    document: (pid: string) => get<unknown>(`/v1/projects/${enc(pid)}/document`).then(asDoc),
    changelog: (pid: string) => get<unknown>(`/v1/projects/${enc(pid)}/changelog`).then(asDoc),
    /** Absolute URL for a download; the browser must send the token, so use `exportBlob`. */
    exportBlob: async (pid: string, format: "md" | "json" | "html"): Promise<Blob> => {
      if (API_MOCK) {
        const doc = await api.results.document(pid);
        return new Blob([doc.markdown], { type: "text/markdown" });
      }
      const token = tokenStore.get();
      const res = await fetch(`${API_URL}/v1/projects/${enc(pid)}/export?format=${format}`, {
        headers: token && !COOKIE_MODE ? { Authorization: `Bearer ${token}` } : {},
        credentials: COOKIE_MODE ? "include" : "omit",
      });
      if (!res.ok) throw parseErrorBody(res.status, await res.text());
      return res.blob();
    },
  },
  review: {
    findings: (pid: string) => get<ListResponse<Finding>>(`/v1/projects/${enc(pid)}/findings`, { limit: 200 }),
    /** Backend answers `{resolution, run, run_denied_reason}`; the mock answers the updated Finding. */
    resolve: async (pid: string, fid: string, status: ResolutionStatus, note: string): Promise<ResolveResult> => {
      const raw = await post<Record<string, unknown>>(`/v1/projects/${enc(pid)}/findings/${enc(fid)}/resolution`, { status, note });
      if ("title" in raw) return { resolution: (raw as unknown as Finding).resolution ?? null, run: null, run_denied_reason: null };
      return raw as unknown as ResolveResult;
    },
    comments: (pid: string, itemId?: string) =>
      get<ListResponse<Comment>>(`/v1/projects/${enc(pid)}/comments`, { item_id: itemId, limit: 200 }),
    comment: (pid: string, itemId: string, text: string) =>
      post<Comment>(`/v1/projects/${enc(pid)}/comments`, { item_id: itemId, text }),
  },
};

async function streamRun(rid: string, onEvent: (e: RunEvent) => void, signal?: AbortSignal): Promise<void> {
  if (API_MOCK) {
    const { mockStream } = await import("./mock/server");
    return mockStream(rid, onEvent, signal);
  }
  const token = tokenStore.get();
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token && !COOKIE_MODE) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_URL}/v1/runs/${enc(rid)}/events`, {
    headers, signal, credentials: COOKIE_MODE ? "include" : "omit",
  });
  if (!res.ok || !res.body) throw parseErrorBody(res.status, await res.text().catch(() => ""));
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const m of parser.push(value)) {
      try {
        const data = JSON.parse(m.data) as Record<string, unknown>;
        const seq = m.id !== undefined && /^\d+$/.test(m.id) ? { seq: Number(m.id) } : {};
        // The server names the event in the SSE `event:` field; the final `end` event carries the terminal status.
        const type = typeof data.type === "string" ? data.type : m.event === "end" ? "status" : m.event;
        onEvent({ ...data, ...seq, type } as unknown as RunEvent);
      } catch {
        /* ignore malformed event */
      }
    }
  }
}
