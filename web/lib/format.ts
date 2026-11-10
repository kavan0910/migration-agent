export function usd(n: number | null | undefined, digits?: number): string {
  if (n === null || n === undefined) return "n/a";
  const d = digits ?? (n !== 0 && Math.abs(n) < 1 ? 3 : 2);
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: d, maximumFractionDigits: d }).format(n);
}
export const num = (n: number | null | undefined) => (n === null || n === undefined ? "n/a" : new Intl.NumberFormat("en-US").format(n));
export function compact(n: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}
export function relTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const s = Math.round((now - Date.parse(iso)) / 1000);
  if (Number.isNaN(s)) return "unknown";
  const abs = Math.abs(s);
  const fmt = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 45) return "just now";
  if (abs < 3600) return fmt.format(-Math.round(s / 60), "minute");
  if (abs < 86400) return fmt.format(-Math.round(s / 3600), "hour");
  if (abs < 86400 * 30) return fmt.format(-Math.round(s / 86400), "day");
  return new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export function duration(startIso?: string | null, endIso?: string | null): string {
  if (!startIso) return "n/a";
  const s = Math.max(0, Math.round(((endIso ? Date.parse(endIso) : Date.now()) - Date.parse(startIso)) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
export const typeLabel = (t: string) => t.replace(/_/g, " ");
export function evidenceRef(e: { file: string; line_start: number | null; line_end: number | null }): string {
  if (!e.line_start) return e.file;
  return `${e.file}:${e.line_start}${e.line_end && e.line_end !== e.line_start ? `-${e.line_end}` : ""}`;
}
