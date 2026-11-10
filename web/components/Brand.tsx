"use client";
import Link from "next/link";
import { useEffect, useState } from "react";

export function Logo({ href = "/", className }: { href?: string; className?: string }) {
  return (
    <Link href={href} className={`inline-flex items-center gap-2.5 font-mono text-[15px] font-semibold ${className ?? ""}`} aria-label="Migration Agent home">
      <svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">
        <rect x="1" y="1" width="24" height="24" rx="6" stroke="currentColor" strokeWidth="1.6" />
        <circle cx="7.5" cy="8" r="2" fill="var(--accent)" />
        <circle cx="18" cy="9.5" r="2" fill="currentColor" />
        <circle cx="12.5" cy="18" r="2" fill="currentColor" />
        <path d="M9.2 9 16 9.4M8.4 9.9l3 6.4M17 11.3l-3.5 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      </svg>
      migration agent
    </Link>
  );
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    const attr = document.documentElement.getAttribute("data-theme");
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(attr === "light" || attr === "dark" ? attr : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }, []);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button" aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}
      onClick={() => {
        document.documentElement.setAttribute("data-theme", next);
        try { localStorage.setItem("ma-theme", next); } catch { /* ignore */ }
        setTheme(next);
      }}
      className="grid size-10 place-items-center rounded-lg text-ink hover:bg-surface-2"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
        {theme === "dark" ? (<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>) : (<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />)}
      </svg>
    </button>
  );
}
