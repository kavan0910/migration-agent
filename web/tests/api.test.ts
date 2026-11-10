import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, normaliseGraphPage, parseErrorBody } from "@/lib/api";
import { tokenStore } from "@/lib/token";
import { SseParser } from "@/lib/sse";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("api client (real mode)", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    tokenStore._reset();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sends bearer token and JSON body to the section 3 paths", async () => {
    tokenStore.set("tok123");
    fetchMock.mockResolvedValue(json(202, { id: "r1" }));
    await api.runs.start("p1", { type: "analyze", model_profile: "fast", max_usd: 1, incremental: false });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://localhost:8000/v1/projects/p1/runs");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok123");
    expect(JSON.parse(init.body)).toEqual({ type: "analyze", model_profile: "fast", max_usd: 1, incremental: false });
  });

  it("does not send credentials or an Authorization header when signed out", async () => {
    fetchMock.mockResolvedValue(json(200, { access_token: "t" }));
    await api.auth.login("a@b.co", "pw");
    const init = fetchMock.mock.calls[0]![1];
    expect(init.headers.Authorization).toBeUndefined();
    expect(init.credentials).toBe("omit");
  });

  it("builds list and graph queries, dropping empty params", async () => {
    fetchMock.mockResolvedValue(json(200, { version: 3, total: 1, nodes: [], edges: [] }));
    await api.graph.page("p 1", { type: "endpoint", q: "", limit: 200, offset: 0, version: undefined });
    expect(fetchMock.mock.calls[0]![0]).toBe("http://localhost:8000/v1/projects/p%201/graph?type=endpoint&limit=200&offset=0");
  });

  it("maps the error envelope to ApiError", async () => {
    fetchMock.mockResolvedValue(json(404, { error: { code: "not_found", message: "Project not found." } }));
    const err = await api.projects.get("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "not_found", message: "Project not found." });
  });

  it("falls back to a generic message for non-envelope errors", () => {
    expect(parseErrorBody(502, "<html>bad gateway</html>")).toMatchObject({ status: 502, code: "http_error" });
  });

  it("reports network failures as ApiError status 0", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    await expect(api.workspaces.list()).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("posts a git url or multipart upload to /source", async () => {
    fetchMock.mockImplementation(async () => json(200, { id: "p" }));
    await api.projects.setGitSource("p", "https://github.com/o/r");
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({ git_url: "https://github.com/o/r" });
    await api.projects.uploadSource("p", new File(["x"], "a.zip"));
    const init = fetchMock.mock.calls[1]![1];
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers["Content-Type"]).toBeUndefined();
  });

  it("normalises both graph envelopes", () => {
    expect(normaliseGraphPage({ version: 2, nodes: [{ id: "a" }], edges: [] }).total).toBe(1);
    expect(normaliseGraphPage({ version: 2, total: 9, items: [{ id: "a" }] })).toMatchObject({ total: 9, nodes: [{ id: "a" }] });
  });

  it("streams SSE run events with the bearer token", async () => {
    tokenStore.set("tok");
    const enc = new TextEncoder();
    const body = new ReadableStream({
      start(c) {
        c.enqueue(enc.encode('event: progress\ndata: {"type":"progress","stage":"ingest","done":1,"total":2}\n\n'));
        c.enqueue(enc.encode('data: {"type":"status","status":"succeeded"}\n\n'));
        c.close();
      },
    });
    fetchMock.mockResolvedValue(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    const got: unknown[] = [];
    await api.runs.stream("r1", (e) => got.push(e));
    expect(fetchMock.mock.calls[0]![1].headers.Authorization).toBe("Bearer tok");
    expect(got).toEqual([{ type: "progress", stage: "ingest", done: 1, total: 2 }, { type: "status", status: "succeeded" }]);
  });
});

describe("SseParser", () => {
  it("handles chunk boundaries, CRLF, comments and multi-line data", () => {
    const p = new SseParser();
    expect(p.push("event: log\r\ndata: a")).toEqual([]);
    expect(p.push("\r\ndata: b\r\n\r\n: keepalive\n\ndata: c\n\n")).toEqual([
      { event: "log", data: "a\nb", id: undefined },
      { event: "message", data: "c", id: undefined },
    ]);
  });
});

describe("token store", () => {
  it("keeps the token in memory and notifies subscribers", () => {
    tokenStore._reset();
    const seen: Array<string | null> = [];
    const off = tokenStore.subscribe((t) => seen.push(t));
    tokenStore.set("abc");
    tokenStore.set(null);
    off();
    expect(seen).toEqual(["abc", null]);
    expect(localStorage.length).toBe(0);
  });
});
