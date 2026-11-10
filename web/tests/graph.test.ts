import { describe, expect, it } from "vitest";
import { countByType, filterNodes, induced, matchesQuery, neighbourhood, topByDegree } from "@/lib/graphFilter";
import { layoutGraph } from "@/lib/graphLayout";
import type { GraphEdge, GraphNode } from "@/lib/types";

const node = (id: string, type: string, name: string, extra: Partial<GraphNode> = {}): GraphNode => ({ id, type, name, evidence: [], confidence: "low", ...extra });
const edge = (id: string, source: string, target: string): GraphEdge => ({ id, source, target, type: "calls", evidence: [], confidence: "low" });

const nodes = [
  node("COMP-1", "component", "OwnerController", { module: "owner/OwnerController.java", responsibility: "Finds owners" }),
  node("EP-1", "endpoint", "GET /owners", { confidence: "medium" }),
  node("RULE-1", "business_rule", "Telephone must be ten digits", { confidence: "high", quirk: true }),
  node("CFG-1", "config", "server.port"),
  node("COMP-2", "component", "VetController"),
];
const edges = [edge("E1", "COMP-1", "EP-1"), edge("E2", "COMP-1", "RULE-1"), edge("E3", "COMP-2", "COMP-1"), edge("E4", "RULE-1", "GHOST")];

describe("graph filtering", () => {
  it("matches all terms case-insensitively across id, name, module and responsibility", () => {
    expect(matchesQuery(nodes[0]!, "owner java")).toBe(true);
    expect(matchesQuery(nodes[0]!, "FINDS")).toBe(true);
    expect(matchesQuery(nodes[0]!, "owner vet")).toBe(false);
    expect(matchesQuery(nodes[0]!, "   ")).toBe(true);
  });
  it("combines type, query, confidence and flagged filters", () => {
    expect(filterNodes(nodes, { type: "component" }).map((n) => n.id)).toEqual(["COMP-1", "COMP-2"]);
    expect(filterNodes(nodes, { type: "component", q: "vet" }).map((n) => n.id)).toEqual(["COMP-2"]);
    expect(filterNodes(nodes, { minConfidence: "medium" }).map((n) => n.id)).toEqual(["EP-1", "RULE-1"]);
    expect(filterNodes(nodes, { flaggedOnly: true }).map((n) => n.id)).toEqual(["RULE-1"]);
    expect(filterNodes(nodes, {})).toHaveLength(5);
  });
  it("counts by type", () => {
    expect(countByType(nodes)).toEqual({ component: 2, endpoint: 1, business_rule: 1, config: 1 });
  });
  it("drops edges with a missing endpoint", () => {
    expect(induced(nodes, edges).edges.map((e) => e.id)).toEqual(["E1", "E2", "E3"]);
  });
  it("ranks by degree deterministically and caps", () => {
    expect(topByDegree(nodes, edges, 2).map((n) => n.id)).toEqual(["COMP-1", "RULE-1"]);
    expect(topByDegree(nodes, edges, 0)).toEqual([]);
  });
  it("builds a bounded breadth-first neighbourhood", () => {
    const one = neighbourhood(nodes, edges, "EP-1", 1, 10);
    expect(one.nodes.map((n) => n.id).sort()).toEqual(["COMP-1", "EP-1"]);
    const two = neighbourhood(nodes, edges, "EP-1", 2, 10);
    expect(two.nodes.map((n) => n.id).sort()).toEqual(["COMP-1", "COMP-2", "EP-1", "RULE-1"]);
    expect(neighbourhood(nodes, edges, "EP-1", 2, 2).nodes).toHaveLength(2);
    expect(neighbourhood(nodes, edges, "nope", 2, 10).nodes).toEqual([]);
  });
});

describe("graph layout", () => {
  it("is deterministic, finite and stays reasonably bounded", () => {
    const a = layoutGraph(nodes, edges, { focusId: "COMP-1" });
    const b = layoutGraph(nodes, edges, { focusId: "COMP-1" });
    expect([...a.entries()]).toEqual([...b.entries()]);
    for (const p of a.values()) {
      expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
      expect(Math.abs(p.x)).toBeLessThan(5000);
    }
  });
  it("lays out 400 nodes quickly", () => {
    const many = Array.from({ length: 400 }, (_, i) => node(`N-${i}`, "component", `n${i}`));
    const es = many.slice(1).map((n, i) => edge(`e${i}`, n.id, many[Math.floor(i / 3)]!.id));
    const t = performance.now();
    const pos = layoutGraph(many, es);
    expect(pos.size).toBe(400);
    expect(performance.now() - t).toBeLessThan(3000);
  });
  it("handles empty input", () => {
    expect(layoutGraph([], []).size).toBe(0);
  });
});
