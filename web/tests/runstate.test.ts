import { describe, expect, it } from "vitest";
import { emptyRunLive, overallPercent, reduceRunEvent } from "@/lib/runState";
import type { RunEvent } from "@/lib/types";
import { validateArchive, validateGitUrl } from "@/lib/validate";
import { extractHeadings, preprocess, NODE_ID } from "@/lib/markdown";
import { relTime, usd } from "@/lib/format";

describe("run event reducer", () => {
  const events: RunEvent[] = [
    { type: "progress", stage: "ingest", done: 2, total: 2, seq: 1 },
    { type: "progress", stage: "analyze", done: 1, total: 4, seq: 2 },
    { type: "usage", stage: "analyze", task: "t", model: "m", input_tokens: 100, output_tokens: 10, usd: 0.25, seq: 3 },
    { type: "log", level: "info", message: "hello", seq: 4 },
  ];
  it("accumulates stages, spend and logs", () => {
    const s = events.reduce(reduceRunEvent, emptyRunLive());
    expect(s.stageOrder).toEqual(["ingest", "analyze"]);
    expect(s.cost).toBeCloseTo(0.25);
    expect(s.inputTokens).toBe(100);
    expect(s.logs).toHaveLength(1);
    expect(overallPercent(s, 4)).toBe(Math.round(((1 + 0.25) / 4) * 100));
  });
  it("ignores replayed events (same seq) so reconnects do not double count", () => {
    const once = events.reduce(reduceRunEvent, emptyRunLive());
    const twice = events.reduce(reduceRunEvent, once);
    expect(twice.cost).toBeCloseTo(0.25);
    expect(twice.logs).toHaveLength(1);
  });
  it("records the terminal status and error", () => {
    const s = reduceRunEvent(emptyRunLive(), { type: "status", status: "stopped_budget", error: "budget reached" });
    expect(s).toMatchObject({ status: "stopped_budget", error: "budget reached" });
  });
});

describe("validation and formatting", () => {
  it("accepts only https git urls", () => {
    expect(validateGitUrl("https://github.com/o/r")).toBeNull();
    expect(validateGitUrl("http://github.com/o/r")).toMatch(/https/);
    expect(validateGitUrl("git@github.com:o/r.git")).toMatch(/full URL/);
  });
  it("checks archive type and size", () => {
    expect(validateArchive({ name: "a.tar.gz", size: 10 })).toBeNull();
    expect(validateArchive({ name: "a.rar", size: 10 })).toMatch(/zip/);
    expect(validateArchive({ name: "a.zip", size: 300 * 1024 * 1024 })).toMatch(/200 MB/);
  });
  it("extracts headings outside code fences and recognises node ids", () => {
    const md = "# T\n## 1. Alpha\n```\n## not a heading\n```\n### Beta\n";
    expect(extractHeadings(md)).toEqual([{ level: 2, text: "1. Alpha", id: "1-alpha" }, { level: 3, text: "Beta", id: "beta" }]);
    expect(preprocess('<a id="X"></a>**X**')).toBe("**X**");
    expect(NODE_ID.test("RULE-0004")).toBe(true);
    expect(NODE_ID.test("hello")).toBe(false);
  });
  it("formats money and relative time", () => {
    expect(usd(0.077)).toBe("$0.077");
    expect(usd(12.4)).toBe("$12.40");
    expect(relTime(new Date(Date.now() - 3 * 86400000).toISOString())).toBe("3 days ago");
  });
});
