/**
 * In-browser mock of the section 3 HTTP API. It is only ever imported when NEXT_PUBLIC_API_MOCK=1.
 * It is stateful for the lifetime of the page (state is rebuilt on a full reload; client-side navigation keeps it).
 */
import { filterNodes } from "../graphFilter";
import type { MockRequest, MockResponse } from "../api";
import type {
  AuditEntry, Comment, Entitlements, Finding, GraphDiff, GraphNode, Member, ModelProfile, Project, ResolutionStatus,
  Role, Run, RunEvent, RunStatus, RunType, Usage, UsageRun, User, Workspace,
} from "../types";
import { TERMINAL_STATUSES } from "../types";
import { DJANGO_FINDINGS, PETCLINIC_FINDINGS, buildDjango, buildLedger, buildPetclinic, uuid, type FixtureGraph, type VNode } from "./data";
import { renderUnderstanding } from "./document";

const DAY = 86_400_000;
let latency = 140;
export function setMockLatency(ms: number) {
  latency = ms;
}

interface MockUser extends User {
  password: string;
}
interface ProjectMeta {
  lines: number;
  graphKey: "petclinic" | "django" | "ledger" | null;
}
interface RunSim {
  events: RunEvent[];
  subs: Set<(e: RunEvent) => void>;
  timer?: ReturnType<typeof setInterval>;
  tick: number;
}
interface State {
  users: MockUser[];
  workspaces: Workspace[];
  members: Record<string, Member[]>;
  projects: Project[];
  meta: Record<string, ProjectMeta>;
  graphs: Record<string, FixtureGraph>;
  runs: Run[];
  sims: Record<string, RunSim>;
  comments: Record<string, Comment[]>;
  findings: Record<string, Finding[]>;
  audit: AuditEntry[];
}

let state: State | null = null;
export function resetMock() {
  if (state) Object.values(state.sims).forEach((s) => s.timer && clearInterval(s.timer));
  state = null;
}

const iso = (ms: number) => new Date(ms).toISOString();
const err = (status: number, code: string, message: string): MockResponse => ({ status, body: { error: { code, message } } });
const ok = (body: unknown, status = 200): MockResponse => ({ status, body });

const graphCache = new Map<string, FixtureGraph>();
function fixtureGraph(key: "petclinic" | "django" | "ledger"): FixtureGraph {
  let g = graphCache.get(key);
  if (!g) {
    g = key === "petclinic" ? buildPetclinic() : key === "django" ? buildDjango() : buildLedger();
    graphCache.set(key, g);
  }
  return g;
}

function init(): State {
  const now = Date.now();
  const demo: MockUser = { id: uuid("user-demo"), email: "demo@example.com", name: "Demo Engineer", password: "" };
  const maya: MockUser = { id: uuid("user-maya"), email: "maya@acme.test", name: "Maya Okafor", password: "" };
  const dev: MockUser = { id: uuid("user-dev"), email: "dev@acme.test", name: "Dev Patel", password: "" };
  const ines: MockUser = { id: uuid("user-ines"), email: "ines@acme.test", name: "Ines Duarte", password: "" };
  const wid = uuid("ws-acme");
  const workspaces: Workspace[] = [{ id: wid, name: "Acme Platform Engineering", role: "owner", created_at: iso(now - 90 * DAY) }];
  const mk = (u: MockUser, role: Role, ago: number): Member => ({ user_id: u.id, email: u.email, name: u.name, role, created_at: iso(now - ago * DAY) });
  const pc = uuid("proj-petclinic"), dj = uuid("proj-django"), ld = uuid("proj-ledger");
  const projects: Project[] = [
    { id: pc, workspace_id: wid, name: "spring-petclinic", source_kind: "git", source_ref: "https://github.com/spring-projects/spring-petclinic", created_at: iso(now - 21 * DAY), updated_at: iso(now - 1 * DAY), last_run_status: "succeeded" },
    { id: dj, workspace_id: wid, name: "django-realworld-example-app", source_kind: "git", source_ref: "https://github.com/gothinkster/django-realworld-example-app", created_at: iso(now - 14 * DAY), updated_at: iso(now - 3 * DAY), last_run_status: "succeeded" },
    { id: ld, workspace_id: wid, name: "ledger-core (5k-node stress fixture)", source_kind: "upload", source_ref: "ledger-core-2026-10.tar.gz", created_at: iso(now - 5 * DAY), updated_at: iso(now - 2 * DAY), last_run_status: "succeeded" },
  ];
  const meta: Record<string, ProjectMeta> = {
    [pc]: { lines: 4297, graphKey: "petclinic" }, [dj]: { lines: 1778, graphKey: "django" }, [ld]: { lines: 412_000, graphKey: "ledger" },
  };
  const run = (pid: string, type: RunType, profile: ModelProfile, cost: number, ago: number, secs: number, status: RunStatus = "succeeded", max: number | null = 1): Run => ({
    id: uuid(`run-${pid}-${type}-${ago}`), project_id: pid, type, model_profile: profile, status, max_usd: max, cost_usd: cost, incremental: false,
    engine_version: "0.2.0", created_at: iso(now - ago * DAY), started_at: iso(now - ago * DAY + 2000), finished_at: iso(now - ago * DAY + secs * 1000), error: null,
  });
  const runs: Run[] = [
    run(pc, "analyze", "balanced", 0.077, 21, 51), run(pc, "verify", "balanced", 0.031, 12, 22), run(pc, "reanalyze", "balanced", 0.052, 1, 38),
    run(dj, "analyze", "fast", 0.021, 14, 29), run(dj, "analyze", "fast", 0.009, 3, 12, "stopped_budget", 0.01),
    run(ld, "analyze", "balanced", 9.84, 2, 1540, "succeeded", 15),
  ];
  runs[3]!.incremental = false;
  const graphs: Record<string, FixtureGraph> = { [pc]: fixtureGraph("petclinic"), [dj]: fixtureGraph("django"), [ld]: fixtureGraph("ledger") };
  const sync = (p: Project) => {
    const g = graphs[p.id];
    if (g) {
      p.node_count = g.nodes.filter((n) => n.until === undefined).length;
    }
  };
  projects.forEach(sync);
  const findings: Record<string, Finding[]> = {
    [pc]: structuredClone(PETCLINIC_FINDINGS), [dj]: structuredClone(DJANGO_FINDINGS), [ld]: [],
  };
  projects.forEach((p) => (p.open_findings = (findings[p.id] ?? []).filter((f) => !f.resolution || f.resolution.status === "open").length));
  return {
    users: [demo, maya, dev, ines], workspaces,
    members: { [wid]: [mk(demo, "owner", 90), mk(maya, "admin", 80), mk(dev, "member", 40), mk(ines, "viewer", 9)] },
    projects, meta, graphs, runs, sims: {},
    comments: {
      [pc]: [
        { id: uuid("c1"), item_id: "RULE-0004", text: "Confirmed with product: the page size should be 10. Fixing the code, not the doc.", author: "maya@acme.test", created_at: iso(now - 2 * DAY) },
        { id: uuid("c2"), item_id: "RULE-0001", text: "We have EU customers, please treat this as a real bug.", author: "dev@acme.test", created_at: iso(now - 1 * DAY) },
      ],
    },
    findings,
    audit: [
      { id: uuid("a1"), actor: "demo@example.com", action: "project.create", target: "ledger-core", at: iso(now - 5 * DAY) },
      { id: uuid("a2"), actor: "maya@acme.test", action: "member.add", target: "ines@acme.test", at: iso(now - 9 * DAY) },
      { id: uuid("a3"), actor: "dev@acme.test", action: "run.start", target: "spring-petclinic", at: iso(now - 1 * DAY) },
    ],
  };
}

const S = (): State => (state ??= init());

/* ------------------------------------------------------------------------------------------------ helpers */

function currentUser(token: string | null): MockUser | null {
  if (!token?.startsWith("mock.")) return null;
  const id = token.slice(5);
  return S().users.find((u) => u.id === id) ?? null;
}
const publicUser = ({ id, email, name }: MockUser): User => ({ id, email, name });
const page = <T,>(items: T[], q: Record<string, string>): { items: T[]; total: number } => {
  const limit = Math.min(200, Math.max(1, Number(q.limit ?? 50) || 50));
  const offset = Math.max(0, Number(q.offset ?? 0) || 0);
  return { items: items.slice(offset, offset + limit), total: items.length };
};

function activeAt(g: FixtureGraph, v: number) {
  const nodes = g.nodes.filter((n) => n.since <= v && (n.until === undefined || n.until > v));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = g.edges.filter((e) => e.since <= v && (e.until === undefined || e.until > v) && ids.has(e.source) && ids.has(e.target));
  return { nodes, edges };
}
const strip = ({ since: _s, until: _u, changedIn: _c, ...n }: VNode): GraphNode => (void _s, void _u, void _c, n);

function latestVersion(g: FixtureGraph) {
  return g.versions[g.versions.length - 1] ?? 1;
}

function projectOf(pid: string, u: MockUser): Project | null {
  const p = S().projects.find((x) => x.id === pid);
  if (!p) return null;
  const member = (S().members[p.workspace_id] ?? []).some((m) => m.user_id === u.id);
  return member ? p : null;
}
function roleOf(wid: string, u: MockUser): Role | null {
  return (S().members[wid] ?? []).find((m) => m.user_id === u.id)?.role ?? null;
}

function entitlements(wid: string): Entitlements {
  const s = S();
  const projs = s.projects.filter((p) => p.workspace_id === wid);
  const ids = new Set(projs.map((p) => p.id));
  const monthStart = Date.now() - 30 * DAY;
  const usd = s.runs.filter((r) => ids.has(r.project_id) && Date.parse(r.created_at) >= monthStart).reduce((a, r) => a + r.cost_usd, 0);
  return {
    plan: "internal", billing_enabled: false,
    limits: { monthly_usd: 25, projects: 10, max_lines_per_project: 500_000, concurrent_runs: 1, members: 10 },
    consumption: {
      monthly_usd: Math.round(usd * 1000) / 1000, projects: projs.length,
      concurrent_runs: s.runs.filter((r) => ids.has(r.project_id) && (r.status === "running" || r.status === "queued")).length,
      members: (s.members[wid] ?? []).length,
    },
  };
}

const PROFILE_MULT: Record<ModelProfile, number> = { fast: 0.4, balanced: 1, best: 2.6 };
function estimate(p: Project, profile: ModelProfile, incremental: boolean) {
  const m = S().meta[p.id];
  if (!m || p.source_kind === "none") return { chunks: 0, usd: 0, allowed: false, reason: "Add a source (git URL or upload) before starting a run." };
  const chunks = Math.max(1, Math.ceil(m.lines / 1500)) * (incremental ? 0.25 : 1);
  const usd = Math.round(chunks * 0.027 * PROFILE_MULT[profile] * 1000) / 1000;
  const ent = entitlements(p.workspace_id);
  const remaining = (ent.limits.monthly_usd ?? Infinity) - (ent.consumption.monthly_usd ?? 0);
  const busy = S().runs.some((r) => r.project_id === p.id && (r.status === "running" || r.status === "queued"));
  const c = Math.ceil(chunks);
  if (busy) return { chunks: c, usd, allowed: false, reason: "A run is already in progress for this project." };
  if (m.lines > (ent.limits.max_lines_per_project ?? Infinity)) return { chunks: c, usd, allowed: false, reason: "Source exceeds the 500,000 line limit of the current plan." };
  if (usd > remaining) return { chunks: c, usd, allowed: false, reason: `Estimated cost $${usd.toFixed(2)} exceeds the $${remaining.toFixed(2)} left in this month's allowance. Try a faster model profile or an incremental run.` };
  return { chunks: c, usd, allowed: true, reason: null };
}

/* ------------------------------------------------------------------------------------------------ run simulation */

const STAGES = ["ingest", "extract", "analyze", "verify", "resolve"];
function startSim(run: Run, p: Project) {
  const s = S();
  const sim: RunSim = { events: [], subs: new Set(), tick: 0 };
  s.sims[run.id] = sim;
  const est = estimate({ ...p }, run.model_profile, !!run.incremental);
  const total = Math.max(2, Math.min(12, est.chunks || 3));
  const stages = run.type === "verify" ? ["ingest", "verify", "resolve"] : STAGES.filter((x) => x !== "verify");
  const plan: RunEvent[] = [{ type: "log", level: "info", message: `Run ${run.id.slice(0, 8)} accepted (${run.type}, ${run.model_profile})` }];
  stages.forEach((stage) => {
    const steps = stage === "analyze" || stage === "verify" ? total : 2;
    for (let i = 1; i <= steps; i++) {
      plan.push({ type: "progress", stage, done: i, total: steps });
      if (stage === "analyze" || stage === "verify") {
        const inTok = 8000 + i * 733, outTok = 900 + i * 61;
        plan.push({ type: "usage", stage, task: stage === "verify" ? "check_claims" : "extract_rules", model: run.model_profile === "best" ? "frontier-large" : "balanced-medium", input_tokens: inTok, output_tokens: outTok, usd: Math.max(0.0005, est.usd / total) });
      }
    }
    plan.push({ type: "log", level: "info", message: `${stage} complete` });
  });
  const budgetCut = run.max_usd !== null && run.max_usd < est.usd * 0.6;
  const cutAt = budgetCut ? Math.floor(plan.length * 0.6) : Infinity;
  const emit = (e: RunEvent) => {
    const withSeq = { ...e, seq: sim.events.length + 1 } as RunEvent;
    sim.events.push(withSeq);
    sim.subs.forEach((f) => f(withSeq));
  };
  const finish = (status: RunStatus, error: string | null = null) => {
    clearInterval(sim.timer);
    run.status = status; run.finished_at = iso(Date.now()); run.error = error;
    p.last_run_status = status; p.updated_at = iso(Date.now());
    if (status === "succeeded" && !s.graphs[p.id]) {
      s.graphs[p.id] = fixtureGraph("django");
      s.findings[p.id] ??= structuredClone(DJANGO_FINDINGS).map((f) => ({ ...f, resolution: null }));
    }
    const g = s.graphs[p.id];
    if (g) p.node_count = g.nodes.filter((n) => n.until === undefined).length;
    p.open_findings = (s.findings[p.id] ?? []).filter((f) => !f.resolution || f.resolution.status === "open").length;
    emit({ type: "status", status, error });
  };
  sim.timer = setInterval(() => {
    if (run.status === "cancelled") return finish("cancelled");
    if (sim.tick === 0) { run.status = "running"; run.started_at = iso(Date.now()); p.last_run_status = "running"; emit({ type: "status", status: "running" }); }
    const ev = plan[sim.tick];
    if (sim.tick >= cutAt) return finish("stopped_budget", `Stopped: the $${run.max_usd?.toFixed(2)} budget for this run was reached.`);
    if (!ev) return finish("succeeded");
    if (ev.type === "usage") run.cost_usd = Math.round((run.cost_usd + ev.usd) * 10000) / 10000;
    emit(ev);
    sim.tick++;
  }, Math.max(latency * 2, 60));
}

export async function mockStream(rid: string, onEvent: (e: RunEvent) => void, signal?: AbortSignal): Promise<void> {
  const sim = S().sims[rid];
  const run = S().runs.find((r) => r.id === rid);
  if (!run) throw Object.assign(new Error("Run not found"), { status: 404, code: "not_found", name: "ApiError" });
  sim?.events.forEach(onEvent);
  if (!sim || TERMINAL_STATUSES.includes(run.status)) {
    if (!sim) onEvent({ type: "status", status: run.status, error: run.error });
    return;
  }
  await new Promise<void>((resolve) => {
    const sub = (e: RunEvent) => {
      onEvent(e);
      if (e.type === "status" && TERMINAL_STATUSES.includes(e.status)) { sim.subs.delete(sub); resolve(); }
    };
    sim.subs.add(sub);
    signal?.addEventListener("abort", () => { sim.subs.delete(sub); resolve(); });
  });
}

function enqueueRun(p: Project, type: RunType, profile: ModelProfile, maxUsd: number | null, incremental: boolean): Run {
  const run: Run = {
    id: crypto.randomUUID(), project_id: p.id, type, model_profile: profile, status: "queued", max_usd: maxUsd, cost_usd: 0,
    incremental, engine_version: "0.2.0", created_at: iso(Date.now()), started_at: null, finished_at: null, error: null,
  };
  S().runs.unshift(run);
  p.last_run_status = "queued";
  startSim(run, p);
  return run;
}

/* ------------------------------------------------------------------------------------------------ router */

type Handler = (r: MockRequest & { m: RegExpMatchArray; u: MockUser }) => MockResponse | Promise<MockResponse>;
const routes: Array<[string, RegExp, Handler, boolean?]> = [];
const route = (method: string, pattern: string, h: Handler, open = false) => {
  routes.push([method, new RegExp("^" + pattern.replace(/\{(\w+)\}/g, "([^/]+)") + "$"), h, open]);
};
const body = <T,>(r: MockRequest) => (r.body ?? {}) as T;
const text = (v: unknown) => (typeof v === "string" ? v.trim() : "");

route("GET", "/healthz", () => ok({ status: "ok" }), true);

route("POST", "/v1/auth/signup", (r) => {
  const { email, password, name } = body<{ email?: string; password?: string; name?: string }>(r);
  if (!/^\S+@\S+\.\S+$/.test(text(email))) return err(422, "validation_error", "Enter a valid email address.");
  if (!password || password.length < 8) return err(422, "validation_error", "Password must be at least 8 characters.");
  if (S().users.some((u) => u.email.toLowerCase() === text(email).toLowerCase())) return err(409, "email_taken", "An account with this email already exists.");
  const u: MockUser = { id: crypto.randomUUID(), email: text(email), name: text(name) || null, password };
  S().users.push(u);
  const ws: Workspace = { id: crypto.randomUUID(), name: `${text(name) || text(email).split("@")[0]}'s workspace`, role: "owner", created_at: iso(Date.now()) };
  S().workspaces.push(ws);
  S().members[ws.id] = [{ user_id: u.id, email: u.email, name: u.name, role: "owner", created_at: iso(Date.now()) }];
  return ok({ access_token: `mock.${u.id}`, token_type: "bearer", user: publicUser(u) }, 201);
}, true);

route("POST", "/v1/auth/login", (r) => {
  const { email, password } = body<{ email?: string; password?: string }>(r);
  const u = S().users.find((x) => x.email.toLowerCase() === text(email).toLowerCase());
  // demo users accept any password so the UI can be explored; created users must match.
  if (!u || (u.password && u.password !== password)) return err(401, "invalid_credentials", "Invalid email or password.");
  return ok({ access_token: `mock.${u.id}`, token_type: "bearer", user: publicUser(u) });
}, true);

route("GET", "/v1/me", (r) => ok(publicUser(r.u)));

route("GET", "/v1/workspaces", (r) => {
  const mine = S().workspaces.filter((w) => roleOf(w.id, r.u)).map((w) => ({ ...w, role: roleOf(w.id, r.u)! }));
  return ok({ items: mine, total: mine.length });
});
route("POST", "/v1/workspaces", (r) => {
  const name = text(body<{ name?: string }>(r).name);
  if (!name) return err(422, "validation_error", "Workspace name is required.");
  const ws: Workspace = { id: crypto.randomUUID(), name, role: "owner", created_at: iso(Date.now()) };
  S().workspaces.push(ws);
  S().members[ws.id] = [{ user_id: r.u.id, email: r.u.email, name: r.u.name, role: "owner" }];
  return ok(ws, 201);
});
route("GET", "/v1/workspaces/{wid}", (r) => {
  const w = S().workspaces.find((x) => x.id === r.m[1]);
  const role = w && roleOf(w.id, r.u);
  return w && role ? ok({ ...w, role }) : err(404, "not_found", "Workspace not found.");
});
route("GET", "/v1/workspaces/{wid}/members", (r) => {
  const wid = r.m[1]!;
  if (!roleOf(wid, r.u)) return err(404, "not_found", "Workspace not found.");
  const items = S().members[wid] ?? [];
  return ok({ items, total: items.length });
});
route("POST", "/v1/workspaces/{wid}/members", (r) => {
  const wid = r.m[1]!;
  const role = roleOf(wid, r.u);
  if (!role) return err(404, "not_found", "Workspace not found.");
  if (role !== "owner" && role !== "admin") return err(403, "forbidden", "Only owners and admins can add members.");
  const { email, role: newRole } = body<{ email?: string; role?: Role }>(r);
  if (!/^\S+@\S+\.\S+$/.test(text(email))) return err(422, "validation_error", "Enter a valid email address.");
  if (!newRole || !["owner", "admin", "member", "viewer"].includes(newRole)) return err(422, "validation_error", "Choose a role.");
  const list = (S().members[wid] ??= []);
  if (list.some((m) => m.email.toLowerCase() === text(email).toLowerCase())) return err(409, "already_member", "That person is already a member.");
  let u = S().users.find((x) => x.email.toLowerCase() === text(email).toLowerCase());
  if (!u) { u = { id: crypto.randomUUID(), email: text(email), name: null, password: "" }; S().users.push(u); }
  const m: Member = { user_id: u.id, email: u.email, name: u.name, role: newRole, created_at: iso(Date.now()) };
  list.push(m);
  return ok(m, 201);
});
route("DELETE", "/v1/workspaces/{wid}/members/{uid}", (r) => {
  const wid = r.m[1]!;
  const role = roleOf(wid, r.u);
  if (!role) return err(404, "not_found", "Workspace not found.");
  if (role !== "owner" && role !== "admin") return err(403, "forbidden", "Only owners and admins can remove members.");
  const list = S().members[wid] ?? [];
  const target = list.find((m) => m.user_id === r.m[2]);
  if (!target) return err(404, "not_found", "Member not found.");
  if (target.role === "owner" && list.filter((m) => m.role === "owner").length === 1) return err(409, "last_owner", "A workspace needs at least one owner.");
  S().members[wid] = list.filter((m) => m !== target);
  return ok(undefined, 204);
});
route("GET", "/v1/workspaces/{wid}/usage", (r) => {
  const wid = r.m[1]!;
  if (!roleOf(wid, r.u)) return err(404, "not_found", "Workspace not found.");
  const pids = new Map(S().projects.filter((p) => p.workspace_id === wid).map((p) => [p.id, p.name]));
  const from = r.query.from ? Date.parse(r.query.from) : -Infinity;
  const to = r.query.to ? Date.parse(r.query.to) : Infinity;
  const runs: UsageRun[] = S().runs.filter((x) => pids.has(x.project_id) && Date.parse(x.created_at) >= from && Date.parse(x.created_at) <= to)
    .map((x) => ({ run_id: x.id, project_id: x.project_id, project_name: pids.get(x.project_id), type: x.type, usd: x.cost_usd, at: x.created_at, input_tokens: Math.round(x.cost_usd * 5_200_000), output_tokens: Math.round(x.cost_usd * 480_000) }));
  const totals = runs.reduce((a, x) => ({ usd: a.usd + x.usd, input_tokens: a.input_tokens + x.input_tokens, output_tokens: a.output_tokens + x.output_tokens, runs: a.runs + 1 }), { usd: 0, input_tokens: 0, output_tokens: 0, runs: 0 });
  const u: Usage = { from: r.query.from ?? null, to: r.query.to ?? null, totals: { ...totals, usd: Math.round(totals.usd * 1000) / 1000 }, runs };
  return ok(u);
});
route("GET", "/v1/workspaces/{wid}/entitlements", (r) => (roleOf(r.m[1]!, r.u) ? ok(entitlements(r.m[1]!)) : err(404, "not_found", "Workspace not found.")));
route("GET", "/v1/workspaces/{wid}/audit", (r) => {
  const role = roleOf(r.m[1]!, r.u);
  if (!role) return err(404, "not_found", "Workspace not found.");
  if (role !== "owner" && role !== "admin") return err(403, "forbidden", "Only admins can read the audit log.");
  return ok(page(S().audit, r.query));
});

route("GET", "/v1/workspaces/{wid}/projects", (r) => {
  if (!roleOf(r.m[1]!, r.u)) return err(404, "not_found", "Workspace not found.");
  return ok(page(S().projects.filter((p) => p.workspace_id === r.m[1]).sort((a, b) => (b.updated_at ?? b.created_at).localeCompare(a.updated_at ?? a.created_at)), r.query));
});
route("POST", "/v1/workspaces/{wid}/projects", (r) => {
  const wid = r.m[1]!;
  const role = roleOf(wid, r.u);
  if (!role) return err(404, "not_found", "Workspace not found.");
  if (role === "viewer") return err(403, "forbidden", "Viewers cannot create projects.");
  const name = text(body<{ name?: string }>(r).name);
  if (name.length < 2 || name.length > 80) return err(422, "validation_error", "Project name must be 2 to 80 characters.");
  const p: Project = { id: crypto.randomUUID(), workspace_id: wid, name, source_kind: "none", source_ref: null, created_at: iso(Date.now()), updated_at: iso(Date.now()), last_run_status: null, node_count: null, open_findings: null };
  S().projects.push(p);
  S().meta[p.id] = { lines: 0, graphKey: null };
  S().audit.unshift({ id: crypto.randomUUID(), actor: r.u.email, action: "project.create", target: name, at: iso(Date.now()) });
  return ok(p, 201);
});
const withProject = (h: (r: MockRequest & { m: RegExpMatchArray; u: MockUser }, p: Project) => MockResponse | Promise<MockResponse>): Handler => (r) => {
  const p = projectOf(r.m[1]!, r.u);
  return p ? h(r, p) : err(404, "not_found", "Project not found.");
};
route("GET", "/v1/projects/{pid}", withProject((_r, p) => ok(p)));
route("PATCH", "/v1/projects/{pid}", withProject((r, p) => {
  const name = text(body<{ name?: string }>(r).name);
  if (name.length < 2) return err(422, "validation_error", "Project name must be 2 to 80 characters.");
  p.name = name;
  return ok(p);
}));
route("DELETE", "/v1/projects/{pid}", withProject((r, p) => {
  const role = roleOf(p.workspace_id, r.u);
  if (role !== "owner" && role !== "admin") return err(403, "forbidden", "Only owners and admins can delete projects.");
  const s = S();
  s.projects = s.projects.filter((x) => x !== p);
  s.runs = s.runs.filter((x) => x.project_id !== p.id);
  delete s.graphs[p.id]; delete s.findings[p.id]; delete s.comments[p.id]; delete s.meta[p.id];
  s.audit.unshift({ id: crypto.randomUUID(), actor: r.u.email, action: "project.delete", target: p.name, at: iso(Date.now()) });
  return ok(undefined, 204);
}));

const PRIVATE_HOST = /^(localhost|.*\.local|.*\.internal|127\.|10\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|\[?::1\]?$)/i;
route("POST", "/v1/projects/{pid}/source", withProject((r, p) => {
  const b = r.body;
  if (typeof FormData !== "undefined" && b instanceof FormData) {
    const f = b.get("file");
    if (!(f instanceof File)) return err(422, "validation_error", "Choose a .zip or .tar.gz file.");
    if (!/\.(zip|tar\.gz|tgz)$/i.test(f.name)) return err(422, "unsupported_media", "Only .zip, .tar.gz and .tgz archives are accepted.");
    if (f.size > 200 * 1024 * 1024) return err(422, "too_large", "Archive exceeds the 200 MB limit.");
    p.source_kind = "upload"; p.source_ref = f.name;
  } else {
    const url = text((b as { git_url?: string } | undefined)?.git_url);
    let u: URL;
    try { u = new URL(url); } catch { return err(422, "validation_error", "Enter a full repository URL, for example https://github.com/org/repo."); }
    if (u.protocol !== "https:") return err(422, "invalid_git_url", "Only https:// repository URLs are accepted.");
    if (PRIVATE_HOST.test(u.hostname) || /^\d+\.\d+\.\d+\.\d+$/.test(u.hostname)) return err(422, "invalid_git_url", "That host is not publicly reachable. Private, loopback and link-local addresses are blocked.");
    p.source_kind = "git"; p.source_ref = u.toString().replace(/\.git$/, "");
  }
  S().meta[p.id] = { lines: 2400 + (p.source_ref?.length ?? 5) * 211, graphKey: S().meta[p.id]?.graphKey ?? null };
  p.updated_at = iso(Date.now());
  return ok(p);
}));
route("POST", "/v1/projects/{pid}/estimate", withProject((r, p) => {
  const b = body<{ model_profile?: ModelProfile; incremental?: boolean }>(r);
  return ok(estimate(p, b.model_profile ?? "balanced", !!b.incremental));
}));

route("GET", "/v1/projects/{pid}/runs", withProject((r, p) => ok(page(S().runs.filter((x) => x.project_id === p.id), r.query))));
route("POST", "/v1/projects/{pid}/runs", withProject((r, p) => {
  const b = body<{ type?: RunType; model_profile?: ModelProfile; max_usd?: number | null; incremental?: boolean }>(r);
  if (!b.type || !["analyze", "verify", "reanalyze"].includes(b.type)) return err(422, "validation_error", "Unknown run type.");
  if (!b.model_profile || !(b.model_profile in PROFILE_MULT)) return err(422, "validation_error", "Choose fast, balanced or best.");
  if (b.max_usd != null && !(b.max_usd > 0)) return err(422, "validation_error", "Budget must be greater than zero.");
  const est = estimate(p, b.model_profile, !!b.incremental);
  if (!est.allowed) return err(409, "run_not_allowed", est.reason ?? "This run is not allowed.");
  const run = enqueueRun(p, b.type, b.model_profile, b.max_usd ?? null, !!b.incremental);
  S().audit.unshift({ id: crypto.randomUUID(), actor: r.u.email, action: "run.start", target: p.name, at: iso(Date.now()) });
  return ok(run, 202);
}));
const findRun = (rid: string, u: MockUser) => {
  const run = S().runs.find((x) => x.id === rid);
  return run && projectOf(run.project_id, u) ? run : null;
};
route("GET", "/v1/runs/{rid}", (r) => { const run = findRun(r.m[1]!, r.u); return run ? ok(run) : err(404, "not_found", "Run not found."); });
route("POST", "/v1/runs/{rid}/cancel", (r) => {
  const run = findRun(r.m[1]!, r.u);
  if (!run) return err(404, "not_found", "Run not found.");
  if (TERMINAL_STATUSES.includes(run.status)) return err(409, "run_finished", "This run has already finished.");
  run.status = "cancelled";
  return ok(run);
});

route("GET", "/v1/projects/{pid}/graph/versions", withProject((_r, p) => {
  const g = S().graphs[p.id];
  const items = (g?.versions ?? []).map((v, i) => {
    const a = g ? activeAt(g, v) : { nodes: [], edges: [] };
    return { version: v, node_count: a.nodes.length, edge_count: a.edges.length, created_at: iso(Date.now() - (g!.versions.length - i) * 4 * DAY) };
  });
  return ok({ items, total: items.length });
}));
route("GET", "/v1/projects/{pid}/graph/diff", withProject((r, p) => {
  const g = S().graphs[p.id];
  const a = Number(r.query.a), b = Number(r.query.b);
  if (!g || !g.versions.includes(a) || !g.versions.includes(b)) return err(404, "not_found", "Unknown graph version.");
  const A = activeAt(g, a), B = activeAt(g, b);
  const ia = new Set(A.nodes.map((n) => n.id)), ib = new Set(B.nodes.map((n) => n.id));
  const lo = Math.min(a, b), hi = Math.max(a, b);
  const d: GraphDiff = {
    a, b,
    added_nodes: B.nodes.filter((n) => !ia.has(n.id)).map(strip),
    removed_nodes: A.nodes.filter((n) => !ib.has(n.id)).map(strip),
    changed_nodes: B.nodes.filter((n) => ia.has(n.id) && n.changedIn?.some((v) => v > lo && v <= hi)).map(strip),
  };
  return ok(d);
}));
route("GET", "/v1/projects/{pid}/graph/nodes/{nid}", withProject((r, p) => {
  const g = S().graphs[p.id];
  const v = g ? latestVersion(g) : 1;
  const act = g ? activeAt(g, v) : { nodes: [], edges: [] };
  const node = act.nodes.find((n) => n.id === r.m[2]);
  if (!node) return err(404, "not_found", "Node not found.");
  const edges = act.edges.filter((e) => e.source === node.id || e.target === node.id);
  const ids = new Set(edges.flatMap((e) => [e.source, e.target]));
  ids.delete(node.id);
  const nb = act.nodes.filter((n) => ids.has(n.id)).slice(0, 60);
  return ok({ node: strip(node), edges: edges.slice(0, 120).map(({ since: _s, until: _u, ...e }) => (void _s, void _u, e)), neighbours: nb.map(strip) });
}));
const degreeCache = new WeakMap<FixtureGraph, Map<string, number>>();
route("GET", "/v1/projects/{pid}/graph", withProject((r, p) => {
  const g = S().graphs[p.id];
  if (!g) return ok({ version: 0, total: 0, nodes: [], edges: [] });
  const v = r.query.version ? Number(r.query.version) : latestVersion(g);
  if (!g.versions.includes(v)) return err(404, "not_found", "Unknown graph version.");
  const act = activeAt(g, v);
  let deg = degreeCache.get(g);
  if (!deg) {
    deg = new Map();
    for (const e of g.edges) { deg.set(e.source, (deg.get(e.source) ?? 0) + 1); deg.set(e.target, (deg.get(e.target) ?? 0) + 1); }
    degreeCache.set(g, deg);
  }
  const d = deg;
  const matched = filterNodes(act.nodes, { type: r.query.type, q: r.query.q }).sort((a, b) => (d.get(b.id) ?? 0) - (d.get(a.id) ?? 0) || a.id.localeCompare(b.id));
  const { items, total } = page(matched, r.query);
  const ids = new Set(items.map((n) => n.id));
  return ok({
    version: v, total, nodes: items.map(strip),
    edges: act.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map(({ since: _s, until: _u, ...e }) => (void _s, void _u, e)),
  });
}));
route("GET", "/v1/projects/{pid}/document", withProject((_r, p) => {
  const g = S().graphs[p.id];
  if (!g) return ok({ markdown: "", version: null });
  const v = latestVersion(g);
  const a = activeAt(g, v);
  return ok({ markdown: renderUnderstanding(p.name, v, a.nodes, a.edges), version: v });
}));
route("GET", "/v1/projects/{pid}/changelog", withProject((_r, p) => {
  const g = S().graphs[p.id];
  return ok({ markdown: g ? `# Changelog\n\n## v${latestVersion(g)}\n\n- Re-analysis after reviewer resolutions.\n- Confidence recalculated from evidence.\n\n## v1\n\n- Initial analysis.\n` : "", version: g ? latestVersion(g) : null });
}));

route("GET", "/v1/projects/{pid}/findings", withProject((r, p) => ok(page(S().findings[p.id] ?? [], r.query))));
route("POST", "/v1/projects/{pid}/findings/{fid}/resolution", withProject((r, p) => {
  const f = (S().findings[p.id] ?? []).find((x) => x.id === r.m[2]);
  if (!f) return err(404, "not_found", "Finding not found.");
  const b = body<{ status?: ResolutionStatus; note?: string }>(r);
  if (!b.status || !["accepted_doc", "accepted_code", "dismissed", "needs_reanalysis"].includes(b.status)) return err(422, "validation_error", "Choose a valid resolution.");
  if ((b.note ?? "").length > 5000) return err(422, "validation_error", "Note is too long (5,000 characters max).");
  f.resolution = { status: b.status, note: b.note ?? "", author: r.u.email, at: iso(Date.now()) };
  p.open_findings = (S().findings[p.id] ?? []).filter((x) => !x.resolution || x.resolution.status === "open").length;
  if ((b.status === "needs_reanalysis" || b.status === "accepted_doc") && !S().runs.some((x) => x.project_id === p.id && (x.status === "running" || x.status === "queued"))) {
    enqueueRun(p, "reanalyze", "balanced", 1, true);
  }
  return ok(f);
}));
route("GET", "/v1/projects/{pid}/comments", withProject((r, p) => {
  const all = (S().comments[p.id] ?? []).filter((c) => !r.query.item_id || c.item_id === r.query.item_id);
  return ok(page([...all].sort((a, b) => a.created_at.localeCompare(b.created_at)), r.query));
}));
route("POST", "/v1/projects/{pid}/comments", withProject((r, p) => {
  const b = body<{ item_id?: string; text?: string }>(r);
  if (!text(b.item_id) || !text(b.text)) return err(422, "validation_error", "Write a comment first.");
  if (text(b.text).length > 5000) return err(422, "validation_error", "Comment is too long (5,000 characters max).");
  const c: Comment = { id: crypto.randomUUID(), item_id: text(b.item_id), text: text(b.text), author: r.u.email, created_at: iso(Date.now()) };
  (S().comments[p.id] ??= []).push(c);
  return ok(c, 201);
}));

export async function handleMock(req: MockRequest): Promise<MockResponse> {
  if (latency > 0) await new Promise((res) => setTimeout(res, latency * (0.6 + Math.random() * 0.8)));
  for (const [method, re, handler, open] of routes) {
    if (method !== req.method) continue;
    const m = req.path.match(re);
    if (!m) continue;
    const u = currentUser(req.token);
    if (!open && !u) return err(401, "unauthorized", "Your session has expired. Sign in again.");
    return handler({ ...req, m, u: u as MockUser });
  }
  return err(404, "not_found", "No such endpoint.");
}
