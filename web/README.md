# Migration Agent web app

Next.js 16 (App Router), React 19, TypeScript strict, Tailwind CSS 4. No UI framework; a small set of accessible primitives lives in `components/ui.tsx`.
All backend access goes through one module, `lib/api.ts`, which follows `docs/platform-contract.md` section 3.

## Run it without a backend (mock mode)

```bash
cd web
npm install
npm run dev:mock          # same as NEXT_PUBLIC_API_MOCK=1 next dev
```

Open http://localhost:3000, choose "Log in" (the demo account is prefilled; any password works). Three sample projects are
loaded: `spring-petclinic` (823 nodes, numbers from `docs/phase0-results.md`), `django-realworld-example-app`, and a
5,200-node synthetic `ledger-core` project to exercise virtualised lists and the graph view. Starting a run plays a simulated
SSE stream. State lives in the page and resets on a hard reload.

## Run it against the backend

```bash
cp .env.example .env.local     # set NEXT_PUBLIC_API_URL, e.g. http://localhost:8000
npm run dev
```

| Variable | Meaning |
|---|---|
| `NEXT_PUBLIC_API_URL` | Backend base URL (default `http://localhost:8000`) |
| `NEXT_PUBLIC_API_MOCK` | `1` serves in-browser fixtures |
| `NEXT_PUBLIC_AUTH_COOKIE` | `1` if the backend uses an httpOnly session cookie (client sends `credentials: include`, no token in JS) |
| `NEXT_PUBLIC_PERSIST_SESSION` | `1` keeps the bearer token in `sessionStorage` across reloads (off by default; on in mock mode) |

Only `NEXT_PUBLIC_*` values are read, so nothing secret can reach the bundle. The backend must allow the web origin via CORS.

## Auth token handling

Bearer token held in module memory (`lib/token.ts`), never `localStorage`. Optional tab-scoped `sessionStorage` persistence is an explicit opt-in.
A cookie mode is wired so the token can move to an httpOnly cookie without UI changes. A 401 from the server clears the session.

## Scripts

`npm run dev | dev:mock | build | start | lint | typecheck | test`

## Layout

```
app/            routes (landing, (auth), (app)/dashboard, projects/[pid]/{,document,graph,findings,settings}, usage, members)
components/     ui primitives, shell, run panel/launcher, graph explorer, canvas, virtual list, node detail
lib/            api.ts (client), sse.ts, token.ts, graphFilter.ts, graphLayout.ts, runState.ts, mock/ (fixtures + fake API)
tests/          Vitest + Testing Library
```
