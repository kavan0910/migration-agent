"use client";
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { cx } from "./ui";

interface T { id: number; text: string; tone: "ok" | "error" }
const Ctx = createContext<{ toast: (text: string, tone?: T["tone"]) => void } | null>(null);
let seq = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<T[]>([]);
  const toast = useCallback((text: string, tone: T["tone"] = "ok") => {
    const id = ++seq;
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 5000);
  }, []);
  const value = useMemo(() => ({ toast }), [toast]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4" role="status" aria-live="polite">
        {items.map((i) => (
          <div key={i.id} className={cx("rise pointer-events-auto rounded-lg border px-4 py-2.5 text-sm shadow-[var(--shadow)]", i.tone === "ok" ? "border-line-strong bg-ink text-bg" : "border-danger bg-danger-soft text-ink")}>
            {i.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
export function useToast() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast outside ToastProvider");
  return v.toast;
}
