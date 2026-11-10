import type { RunEvent, RunStatus } from "./types";

export interface StageProgress {
  done: number;
  total: number;
}
export interface RunLive {
  stages: Record<string, StageProgress>;
  stageOrder: string[];
  cost: number;
  inputTokens: number;
  outputTokens: number;
  logs: Array<{ level: string; message: string }>;
  status: RunStatus | null;
  error: string | null;
  lastSeq: number;
}
export const emptyRunLive = (): RunLive => ({
  stages: {}, stageOrder: [], cost: 0, inputTokens: 0, outputTokens: 0, logs: [], status: null, error: null, lastSeq: 0,
});

const MAX_LOGS = 200;

/** Pure reducer; ignores events already seen (replay after reconnect) using `seq` when present. */
export function reduceRunEvent(s: RunLive, e: RunEvent): RunLive {
  if (e.seq !== undefined) {
    if (e.seq <= s.lastSeq) return s;
  }
  const next: RunLive = { ...s, lastSeq: e.seq ?? s.lastSeq };
  switch (e.type) {
    case "progress":
      next.stages = { ...s.stages, [e.stage]: { done: e.done, total: e.total } };
      next.stageOrder = s.stageOrder.includes(e.stage) ? s.stageOrder : [...s.stageOrder, e.stage];
      return next;
    case "usage":
      next.cost = s.cost + e.usd;
      next.inputTokens = s.inputTokens + e.input_tokens;
      next.outputTokens = s.outputTokens + e.output_tokens;
      return next;
    case "log":
      next.logs = [...s.logs, { level: e.level, message: e.message }].slice(-MAX_LOGS);
      return next;
    case "status":
      next.status = e.status;
      next.error = e.error ?? null;
      return next;
    default:
      return s;
  }
}

/** Overall percentage across the stages seen so far; stages not yet started are unknown, so this is a floor. */
export function overallPercent(s: RunLive, plannedStages = 4): number {
  const seen = s.stageOrder.length;
  if (seen === 0) return 0;
  const done = s.stageOrder.reduce((a, k) => a + (s.stages[k]!.total ? s.stages[k]!.done / s.stages[k]!.total : 0), 0);
  return Math.min(100, Math.round((done / Math.max(plannedStages, seen)) * 100));
}
