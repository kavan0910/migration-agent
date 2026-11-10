# Web notes (stream C)

## Built
- Pages: landing, sign up, log in, dashboard (projects), new project (git URL or archive upload, client validation, retry-safe), project overview
  (estimate with allowed/denied reason, budget cap, model profile, incremental, start run, live SSE progress with spend/stages/log/cancel,
  run history), understanding document (rendered markdown, contents, node-id links into the graph, export md/json/html), graph explorer
  (server-side search and type filter, version select, virtualised paged list, canvas graph with pan/zoom/focus, node detail with evidence quotes,
  derived confidence, relationships, related findings, comments, version diff), findings review (4 resolutions, note, discussion thread),
  usage and plan (totals, daily spend, per-run table, entitlements, explicit "Billing not enabled yet"), members (add/remove, roles, audit log),
  project settings (rename, delete with typed project name).
- Loading, empty and error states on every data view; light/dark (system + toggle); keyboard operable; skip link; reduced-motion; phone to wide desktop.
- `lib/api.ts` typed client; fetch-based SSE (EventSource cannot send Authorization); mock server implementing the same endpoints with SSRF-style
  source validation, entitlement-based estimate denial, run simulation, versions and diffs.
- 45 unit tests (API client, SSE parser, token store, graph filter/layout, run reducer, validation, mock contract, key components).

## Not built
- Playwright e2e (visual checks were done manually with headless Chromium).
- OIDC login flow (UI uses the password endpoints of `AUTH_MODE=dev`); token refresh; password reset; email invites.
- Graph view is canvas based; it is a visual aid, the list/detail views are the accessible path. No WebGL, no worker-based layout.
- User docs upload UI (`/user-docs` endpoints exist in the contract but no page uses them yet). Changelog endpoint is typed but not surfaced.
- Role-based UI hiding is partial (viewer cannot create/delete, only admins see audit log); the server remains the authority.

## Assumptions where the contract is silent (confirm with stream B)
- Graph list response: `{version,total,nodes|items,edges}`; the client accepts either `nodes` or `items`. Edges are those among the returned nodes.
  Order is server defined; the mock sorts by degree.
- `GET .../graph/nodes/{nid}` returns `{node,edges,neighbours}`. `.../graph/versions` returns `{items:[{version,node_count,edge_count,created_at}],total}`.
  `.../graph/diff` returns `{a,b,added_nodes,removed_nodes,changed_nodes}`. `.../document` returns `{markdown,version}`.
- Finding: `{id,title,severity,detail,quotes:[{text,source}],item_ids,resolution}` (the existing review UI shape plus `resolution`).
  Comment: `{id,item_id,text,author,created_at}`; finding comments use the finding id as `item_id`.
- Usage: `{totals:{usd,input_tokens,output_tokens,runs},runs:[...]}`; entitlements: `{plan,limits,consumption,billing_enabled}`.
- Auth responses: `{access_token,user?}`. Add member body: `{email,role}`; project create body: `{name}` then `POST /source`.
- SSE events are JSON data lines of the engine event shapes plus a final `{type:"status",status}`; `seq` is used to drop replays if present.

## Open problems
- Graph view caps at 400 nodes (O(n^2) layout on the main thread, ~100 ms). Beyond that needs server-side sampling or a worker/WebGL renderer.
- Without httpOnly cookies, a reload requires login again unless session persistence is enabled.
- Node counts per type for the filter chips are not available from the contract; chips show no counts.
- Fonts: Geist (bundled by the `geist` package) and Instrument Serif (`@fontsource`), both self-hosted; no network fetch at build.
- TypeScript is pinned at 5.9.3 and ESLint at 9.x for compatibility with eslint-config-next 16; newer majors were not tried.
