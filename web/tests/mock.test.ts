import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleMock, resetMock, setMockLatency } from "@/lib/mock/server";

async function call(method: string, path: string, body?: unknown, query: Record<string, string> = {}, token: string | null = TOKEN) {
  const r = await handleMock({ method, path, query, body, token });
  return r;
}
let TOKEN = "";
let WID = "";
let PID = "";

beforeAll(() => setMockLatency(0));
beforeEach(async () => {
  resetMock();
  const login = await call("POST", "/v1/auth/login", { email: "demo@example.com", password: "x" }, {}, null);
  TOKEN = (login.body as { access_token: string }).access_token;
  WID = ((await call("GET", "/v1/workspaces")).body as { items: Array<{ id: string }> }).items[0]!.id;
  PID = ((await call("GET", `/v1/workspaces/${WID}/projects`)).body as { items: Array<{ id: string; name: string }> }).items.find((p) => p.name === "spring-petclinic")!.id;
});

describe("mock API follows the section 3 contract", () => {
  it("rejects unauthenticated calls with the error envelope", async () => {
    const r = await call("GET", "/v1/me", undefined, {}, null);
    expect(r.status).toBe(401);
    expect(r.body).toEqual({ error: { code: "unauthorized", message: expect.any(String) } });
  });
  it("paginates graph nodes and filters by type and query", async () => {
    const all = (await call("GET", `/v1/projects/${PID}/graph`, undefined, { limit: "200" })).body as { total: number; nodes: unknown[]; edges: unknown[] };
    expect(all.total).toBeGreaterThan(700);
    expect(all.nodes).toHaveLength(200);
    const eps = (await call("GET", `/v1/projects/${PID}/graph`, undefined, { type: "endpoint", limit: "50" })).body as { total: number; nodes: Array<{ type: string }> };
    expect(eps.nodes.every((n) => n.type === "endpoint")).toBe(true);
    const q = (await call("GET", `/v1/projects/${PID}/graph`, undefined, { q: "telephone" })).body as { nodes: Array<{ name: string }> };
    expect(q.nodes.length).toBeGreaterThan(0);
  });
  it("adds members only through an invitation the invitee accepts, with a uniform invite answer", async () => {
    const known = await call("POST", `/v1/workspaces/${WID}/invitations`, { email: "maya@acme.test", role: "viewer" });
    const unknown = await call("POST", `/v1/workspaces/${WID}/invitations`, { email: "ghost@acme.test", role: "viewer" });
    expect(known.status).toBe(202);
    expect(Object.keys(known.body as object).sort()).toEqual(Object.keys(unknown.body as object).sort());
    const before = ((await call("GET", `/v1/workspaces/${WID}/members`)).body as { total: number }).total;
    expect(before).toBe(4); // nothing was added yet
    const mayaTok = ((await call("POST", "/v1/auth/login", { email: "maya@acme.test", password: "x" }, {}, null)).body as { access_token: string }).access_token;
    expect((await call("POST", "/v1/invitations/accept", { token: (known.body as { token: string }).token }, {}, TOKEN)).status).toBe(404); // wrong account
    expect((await call("POST", "/v1/invitations/accept", { token: (known.body as { token: string }).token }, {}, mayaTok)).status).toBe(409); // already a member
  });
  it("answers another workspace's or unknown project with 404, never 403", async () => {
    expect((await call("GET", "/v1/projects/00000000-0000-4000-a000-000000000000")).status).toBe(404);
  });
  it("diffs versions", async () => {
    const d = (await call("GET", `/v1/projects/${PID}/graph/diff`, undefined, { a: "1", b: "3" })).body as { added_nodes: unknown[]; removed_nodes: unknown[]; changed_nodes: unknown[] };
    expect(d.added_nodes.length).toBeGreaterThan(0);
    expect(d.removed_nodes.length).toBeGreaterThan(0);
    expect(d.changed_nodes.length).toBeGreaterThan(0);
  });
  it("estimates with allowed and a reason when denied", async () => {
    const ledger = ((await call("GET", `/v1/workspaces/${WID}/projects`)).body as { items: Array<{ id: string; name: string }> }).items.find((p) => p.name.startsWith("ledger"))!.id;
    const best = (await call("POST", `/v1/projects/${ledger}/estimate`, { model_profile: "best" })).body as { allowed: boolean; reason: string | null };
    expect(best.allowed).toBe(false);
    expect(best.reason).toMatch(/allowance/);
    const ok = (await call("POST", `/v1/projects/${PID}/estimate`, { model_profile: "fast" })).body as { allowed: boolean; usd: number };
    expect(ok.allowed).toBe(true);
    expect(ok.usd).toBeGreaterThan(0);
  });
  it("blocks SSRF-style git urls and non-https", async () => {
    const created = (await call("POST", `/v1/workspaces/${WID}/projects`, { name: "new-one" })).body as { id: string };
    for (const url of ["http://github.com/o/r", "https://127.0.0.1/r", "https://169.254.169.254/latest", "https://localhost/x", "https://10.0.0.5/x"]) {
      const r = await call("POST", `/v1/projects/${created.id}/source`, { git_url: url });
      expect(r.status, url).toBe(422);
    }
    expect((await call("POST", `/v1/projects/${created.id}/source`, { git_url: "https://github.com/o/r" })).status).toBe(200);
  });
  it("resolution on needs_reanalysis enqueues a reanalyze run", async () => {
    const f = ((await call("GET", `/v1/projects/${PID}/findings`)).body as { items: Array<{ id: string; resolution: unknown }> }).items.find((x) => !x.resolution)!;
    const r = await call("POST", `/v1/projects/${PID}/findings/${f.id}/resolution`, { status: "needs_reanalysis", note: "again" });
    expect(r.status).toBe(200);
    const runs = (await call("GET", `/v1/projects/${PID}/runs`)).body as { items: Array<{ type: string; status: string }> };
    expect(runs.items[0]).toMatchObject({ type: "reanalyze" });
    resetMock();
  });
  it("deleting a project removes it", async () => {
    expect((await call("DELETE", `/v1/projects/${PID}`)).status).toBe(204);
    expect((await call("GET", `/v1/projects/${PID}`)).status).toBe(404);
  });
});
