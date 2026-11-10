"use client";
import Link from "next/link";
import { useEffect, useId, useRef } from "react";
import type { ComponentProps, ReactNode } from "react";
import type { Confidence, RunStatus, Severity } from "@/lib/types";
import { typeLabel } from "@/lib/format";

export const cx = (...a: Array<string | false | null | undefined>) => a.filter(Boolean).join(" ");

/* ------------------------------------------------------------------ buttons */
type Variant = "primary" | "secondary" | "ghost" | "danger";
const BASE = "inline-flex items-center justify-center gap-2 rounded-lg font-medium whitespace-nowrap select-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed min-h-10 px-4 text-sm";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-ink hover:brightness-110 active:brightness-95 shadow-[inset_0_-2px_0_rgba(0,0,0,0.18)]",
  secondary: "bg-surface border border-line-strong text-ink hover:bg-surface-2",
  ghost: "text-ink hover:bg-surface-2",
  danger: "bg-danger text-white hover:brightness-110 dark:text-black",
};
export function Button({ variant = "secondary", loading, className, children, disabled, ...p }: ComponentProps<"button"> & { variant?: Variant; loading?: boolean }) {
  return (
    <button type="button" {...p} disabled={disabled || loading} aria-busy={loading || undefined} className={cx(BASE, VARIANTS[variant], className)}>
      {loading && <Spinner />}
      {children}
    </button>
  );
}
export function LinkButton({ variant = "secondary", className, ...p }: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link {...p} className={cx(BASE, VARIANTS[variant], className)} />;
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cx("size-4 animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/* ------------------------------------------------------------------ forms */
export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium">{label}</label>
      {children}
      {hint && !error && <p className="text-[13px] text-muted">{hint}</p>}
      {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
    </div>
  );
}
const INPUT = "w-full min-h-10 rounded-lg border border-line-strong bg-surface px-3 text-sm placeholder:text-muted/70 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-60";
export function Input({ className, ...p }: ComponentProps<"input">) {
  return <input {...p} className={cx(INPUT, className)} />;
}
export function Select({ className, ...p }: ComponentProps<"select">) {
  return <select {...p} className={cx(INPUT, "pr-8", className)} />;
}
export function Textarea({ className, ...p }: ComponentProps<"textarea">) {
  return <textarea {...p} className={cx(INPUT, "py-2 min-h-20", className)} />;
}

/* ------------------------------------------------------------------ surfaces */
export function Card({ className, ...p }: ComponentProps<"div">) {
  return <div {...p} className={cx("rounded-xl border border-line bg-surface", className)} />;
}
export function PageHeader({ eyebrow, title, actions, children }: { eyebrow?: string; title: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <header className="rise mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-2">{eyebrow}</p>}
        <h1 className="display text-[2.4rem] sm:text-5xl break-words">{title}</h1>
        {children && <div className="mt-2 max-w-2xl text-muted">{children}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cx("skeleton", className)} />;
}
export function LoadingBlock({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-16" />)}
    </div>
  );
}
export function EmptyState({ title, children, action, glyph }: { title: string; children?: ReactNode; action?: ReactNode; glyph?: ReactNode }) {
  return (
    <div className="dotgrid rounded-xl border border-dashed border-line-strong px-6 py-14 text-center">
      <div className="mx-auto mb-4 grid size-12 place-items-center rounded-full border border-line-strong bg-surface text-muted" aria-hidden="true">
        {glyph ?? <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 7h16M4 12h10M4 17h6" strokeLinecap="round" /></svg>}
      </div>
      <h3 className="display text-3xl">{title}</h3>
      {children && <p className="mx-auto mt-2 max-w-md text-muted">{children}</p>}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}
export function ErrorState({ error, onRetry, title = "Could not load this" }: { error: Error; onRetry?: () => void; title?: string }) {
  return (
    <div role="alert" className="rounded-xl border border-danger/40 bg-danger-soft px-5 py-5">
      <p className="font-semibold text-danger">{title}</p>
      <p className="mt-1 text-sm">{error.message}</p>
      {onRetry && <Button className="mt-3" onClick={onRetry}>Try again</Button>}
    </div>
  );
}
export function Notice({ tone = "info", title, children }: { tone?: "info" | "warn" | "danger" | "ok"; title?: string; children: ReactNode }) {
  const t = { info: "bg-accent-soft border-accent/30", warn: "bg-warn-soft border-warn/40", danger: "bg-danger-soft border-danger/40", ok: "bg-ok-soft border-ok/40" }[tone];
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cx("rounded-lg border px-4 py-3 text-sm", t)}>
      {title && <p className="font-semibold">{title}</p>}
      <div>{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ status, confidence, glyphs */
const STATUS: Record<RunStatus, { label: string; cls: string }> = {
  queued: { label: "Queued", cls: "bg-surface-2 text-muted" },
  running: { label: "Running", cls: "bg-accent-soft text-accent" },
  succeeded: { label: "Succeeded", cls: "bg-ok-soft text-ok" },
  failed: { label: "Failed", cls: "bg-danger-soft text-danger" },
  stopped_budget: { label: "Stopped at budget", cls: "bg-warn-soft text-warn" },
  cancelled: { label: "Cancelled", cls: "bg-surface-2 text-muted" },
};
export function StatusPill({ status }: { status: RunStatus | null | undefined }) {
  if (!status) return <span className="eyebrow">No runs</span>;
  const s = STATUS[status];
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium", s.cls)}>
      <span className={cx("size-1.5 rounded-full bg-current", status === "running" && "live-dot")} aria-hidden="true" />
      {s.label}
    </span>
  );
}
export function SeverityTag({ severity }: { severity: Severity }) {
  const c = { high: "bg-danger-soft text-danger", medium: "bg-warn-soft text-warn", low: "bg-ok-soft text-ok" }[severity];
  return <span className={cx("rounded px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide", c)}>{severity}</span>;
}

/** Three-segment meter: confidence is derived from evidence, so it is shown as a reading, not a score. */
export function ConfidenceMeter({ value, showLabel = true }: { value: Confidence; showLabel?: boolean }) {
  const n = { low: 1, medium: 2, high: 3 }[value];
  const color = { low: "var(--warn)", medium: "var(--t-component)", high: "var(--ok)" }[value];
  return (
    <span className="inline-flex items-center gap-2" title={`Confidence ${value}, derived from evidence`}>
      <span className="inline-flex gap-[3px]" aria-hidden="true">
        {[1, 2, 3].map((i) => <span key={i} className="h-2.5 w-3.5 rounded-[2px]" style={{ background: i <= n ? color : "var(--line)" }} />)}
      </span>
      {showLabel ? <span className="font-mono text-xs">{value}</span> : <span className="sr-only">Confidence {value}</span>}
    </span>
  );
}

const SHAPES: Record<string, string> = {
  component: "M3 3h10v10H3z",
  endpoint: "M8 1.5 14.5 8 8 14.5 1.5 8z",
  data_entity: "M8 2c3.3 0 6 1 6 2.2v7.6C14 13 11.3 14 8 14s-6-1-6-2.2V4.2C2 3 4.7 2 8 2zM2 4.2C2 5.4 4.7 6.4 8 6.4s6-1 6-2.2",
  business_rule: "M8 2 14.5 13.5h-13z",
  external_dependency: "M8 1.5 14 5v6l-6 3.5L2 11V5z",
  config: "M3 8h10M8 3v10",
};
export function TypeGlyph({ type, size = 14 }: { type: string; size?: number }) {
  const d = SHAPES[type] ?? "M8 3a5 5 0 1 0 0 10A5 5 0 0 0 8 3z";
  const stroke = type === "config" || type === "data_entity";
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" className="shrink-0" style={{ color: `var(--t-${type}, var(--t-config))` }}>
      <path d={d} fill={stroke ? "none" : "currentColor"} stroke="currentColor" strokeWidth={stroke ? 1.6 : 0} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
export function TypeTag({ type }: { type: string }) {
  return <span className="inline-flex items-center gap-1.5 text-xs text-muted"><TypeGlyph type={type} />{typeLabel(type)}</span>;
}

/* ------------------------------------------------------------------ dialog (native <dialog>: focus trap + Esc for free) */
export function Dialog({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref} aria-labelledby={id} onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-2xl border border-line-strong bg-surface p-0 text-ink shadow-[var(--shadow)]"
    >
      {open && (
        <div className="p-6">
          <h2 id={id} className="display text-3xl">{title}</h2>
          <div className="mt-3">{children}</div>
          {footer && <div className="mt-6 flex flex-wrap justify-end gap-2">{footer}</div>}
        </div>
      )}
    </dialog>
  );
}
