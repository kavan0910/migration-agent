/** Types mirroring docs/platform-contract.md section 3. Where the contract is silent, see NOTES.md "Assumptions". */

export type Role = "owner" | "admin" | "member" | "viewer";

export interface User {
  id: string;
  email: string;
  name?: string | null;
  /** What the backend sends; the client copies it to `name`. */
  display_name?: string | null;
}
export interface TokenResponse {
  access_token: string;
  token_type?: string;
  user?: User;
}
export interface Workspace {
  id: string;
  name: string;
  role?: Role;
  created_at?: string;
}
export interface Member {
  user_id: string;
  email: string;
  name?: string | null;
  display_name?: string | null;
  role: Role;
  created_at?: string;
}
/** Result of creating an invitation. The token is shown once to the inviting admin; the invitee accepts it. */
export interface InviteResult {
  status: string;
  token: string;
  invitation: { id: string; email: string; role: Role; expires_at?: string };
}
export interface ListResponse<T> {
  items: T[];
  total: number;
}

export type SourceKind = "git" | "upload" | "none";
export interface Project {
  id: string;
  workspace_id: string;
  name: string;
  source_kind: SourceKind;
  source_ref?: string | null;
  created_at: string;
  updated_at?: string;
  last_run_status?: RunStatus | null;
  node_count?: number | null;
  open_findings?: number | null;
}

export type RunType = "analyze" | "verify" | "reanalyze";
export type ModelProfile = "fast" | "balanced" | "best";
export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "stopped_budget" | "cancelled";
export const TERMINAL_STATUSES: readonly RunStatus[] = ["succeeded", "failed", "stopped_budget", "cancelled"];

export interface Run {
  id: string;
  project_id: string;
  type: RunType;
  model_profile: ModelProfile;
  status: RunStatus;
  max_usd: number | null;
  cost_usd: number;
  incremental?: boolean;
  engine_version?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  error?: string | null;
}
export interface RunRequest {
  type: RunType;
  model_profile: ModelProfile;
  max_usd: number | null;
  incremental: boolean;
}
export interface Estimate {
  chunks: number;
  usd: number;
  allowed: boolean;
  reason: string | null;
}
export interface EstimateRequest {
  model_profile?: ModelProfile;
  incremental?: boolean;
  type?: RunType;
}

/** SSE payloads: engine events plus a terminal status event emitted by the worker. */
export type RunEvent =
  | { type: "progress"; stage: string; done: number; total: number; seq?: number }
  | { type: "usage"; stage: string; task: string; model: string; input_tokens: number; output_tokens: number; usd: number; seq?: number }
  | { type: "log"; level: string; message: string; seq?: number }
  | { type: "status"; status: RunStatus; error?: string | null; seq?: number };

export type NodeType =
  | "component"
  | "endpoint"
  | "data_entity"
  | "business_rule"
  | "external_dependency"
  | "config"
  | (string & {});
export type Confidence = "low" | "medium" | "high";

export interface Evidence {
  file: string;
  line_start: number | null;
  line_end: number | null;
  symbol?: string | null;
  quote: string;
}
export interface GraphNode {
  id: string;
  type: NodeType;
  name: string;
  module?: string | null;
  qualified_symbol?: string | null;
  responsibility?: string | null;
  evidence: Evidence[];
  quirk?: boolean;
  suspected_bug?: boolean;
  confidence: Confidence;
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  evidence: Evidence[];
  confidence: Confidence;
}
export interface GraphPage {
  version: number;
  /** Total number of nodes matching the filters (before limit/offset). */
  total: number;
  nodes: GraphNode[];
  /** Edges among (or touching) the returned nodes. */
  edges: GraphEdge[];
}
export interface GraphQuery {
  version?: number;
  type?: string;
  q?: string;
  limit?: number;
  offset?: number;
}
export interface NodeDetail {
  node: GraphNode;
  edges: GraphEdge[];
  neighbours: GraphNode[];
}
export interface GraphVersion {
  version: number;
  created_at?: string;
  node_count: number;
  edge_count: number;
  run_id?: string | null;
}
export interface GraphDiff {
  a: number;
  b: number;
  added_nodes: GraphNode[];
  removed_nodes: GraphNode[];
  changed_nodes: GraphNode[];
  added_edges?: GraphEdge[] | number;
  removed_edges?: GraphEdge[] | number;
}
export interface DocumentResponse {
  markdown: string;
  version?: number | null;
}

export type Severity = "high" | "medium" | "low";
export type ResolutionStatus = "accepted_doc" | "accepted_code" | "dismissed" | "needs_reanalysis";
export interface FindingQuote {
  text: string;
  source: string;
}
export interface Resolution {
  status: ResolutionStatus | "open";
  note: string;
  author?: string | null;
  at?: string;
  created_at?: string;
}
export interface ResolveResult {
  resolution: Resolution | null;
  run: Run | null;
  run_denied_reason: string | null;
}
export interface Finding {
  id: string;
  title: string;
  severity: Severity;
  detail?: string;
  quotes: FindingQuote[];
  item_ids: string[];
  resolution?: Resolution | null;
}
export interface Comment {
  id: string;
  item_id: string;
  text: string;
  author?: string | null;
  created_at: string;
}

export interface UsageTotals {
  usd: number;
  input_tokens: number;
  output_tokens: number;
  runs: number;
  events?: number;
}
export interface UsageRun {
  run_id: string;
  project_id: string;
  project_name?: string | null;
  type: RunType;
  usd: number;
  input_tokens: number;
  output_tokens: number;
  at: string;
}
export interface Usage {
  by_run?: UsageRun[];
  from?: string | null;
  to?: string | null;
  totals: UsageTotals;
  runs: UsageRun[];
}
export interface Entitlements {
  plan: string;
  limits: Record<string, number | null>;
  consumption: Record<string, number>;
  billing_enabled?: boolean;
}
export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  target: string;
  at: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}
