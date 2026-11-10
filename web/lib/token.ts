/**
 * Access-token holder.
 *
 * Decision: the token lives in module memory, never in localStorage (readable by any XSS payload).
 * - Cookie mode (NEXT_PUBLIC_AUTH_COOKIE=1): the backend sets an httpOnly cookie, the client never sees a
 *   token and requests use `credentials: "include"`. The in-memory token is simply unused.
 * - Bearer mode (the contract today): memory only. A page reload requires signing in again unless
 *   session persistence is enabled (sessionStorage, tab-scoped; on by default only in mock mode where no
 *   real credential exists). Persistence is an explicit opt-in, see NEXT_PUBLIC_PERSIST_SESSION.
 */
type Listener = (token: string | null) => void;

const KEY = "ma.session";
let memory: string | null = null;
let hydrated = false;
const listeners = new Set<Listener>();

export const persistSession =
  process.env.NEXT_PUBLIC_PERSIST_SESSION === "1" || process.env.NEXT_PUBLIC_API_MOCK === "1";

function hydrate() {
  if (hydrated) return;
  hydrated = true;
  if (!persistSession || typeof window === "undefined") return;
  try {
    memory = window.sessionStorage.getItem(KEY);
  } catch {
    /* storage unavailable: stay in memory */
  }
}

export const tokenStore = {
  get(): string | null {
    hydrate();
    return memory;
  },
  set(token: string | null) {
    hydrate();
    memory = token;
    if (persistSession && typeof window !== "undefined") {
      try {
        if (token) window.sessionStorage.setItem(KEY, token);
        else window.sessionStorage.removeItem(KEY);
      } catch {
        /* ignore */
      }
    }
    listeners.forEach((l) => l(token));
  },
  subscribe(l: Listener): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  /** test helper */
  _reset() {
    memory = null;
    hydrated = false;
    listeners.clear();
  },
};
