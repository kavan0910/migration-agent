"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/hooks";
import { ThemeToggle, Logo } from "./Brand";
import { Button, Field, Input, Notice } from "./ui";
import { API_MOCK } from "@/lib/api";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const { status, login, signup } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState(API_MOCK && mode === "login" ? "demo@example.com" : "");
  const [password, setPassword] = useState(API_MOCK && mode === "login" ? "demo-password" : "");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErr, setFieldErr] = useState<{ email?: string; password?: string }>({});

  useEffect(() => {
    if (status === "authed") router.replace("/dashboard");
  }, [status, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const fe: typeof fieldErr = {};
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) fe.email = "Enter a valid email address.";
    if (password.length < (mode === "signup" ? 10 : 1)) fe.password = mode === "signup" ? "Use at least 10 characters." : "Enter your password.";
    setFieldErr(fe);
    if (fe.email || fe.password) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email.trim(), password);
      else await signup(email.trim(), password, name.trim() || undefined);
      router.replace("/dashboard");
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const isLogin = mode === "login";
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1fr_1.1fr]">
      <div className="flex flex-col px-5 py-6 sm:px-10">
        <div className="flex items-center justify-between"><Logo /><ThemeToggle /></div>
        <main id="main" className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">
          <p className="eyebrow rise mb-3">{isLogin ? "Welcome back" : "Create your workspace"}</p>
          <h1 className="display rise rise-1 text-5xl">{isLogin ? "Log in" : "Start reading your code"}</h1>
          <form onSubmit={submit} noValidate className="rise rise-2 mt-8 space-y-4">
            {!isLogin && (
              <Field label="Name (optional)" htmlFor="name"><Input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
            )}
            <Field label="Work email" htmlFor="email" error={fieldErr.email}>
              <Input id="email" type="email" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!!fieldErr.email} aria-describedby={fieldErr.email ? undefined : undefined} />
            </Field>
            <Field label="Password" htmlFor="password" error={fieldErr.password} hint={isLogin ? undefined : "At least 10 characters."}>
              <Input id="password" type="password" autoComplete={isLogin ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={!!fieldErr.password} />
            </Field>
            {error && <Notice tone="danger">{error}</Notice>}
            <Button type="submit" variant="primary" loading={busy} className="w-full min-h-11">{isLogin ? "Log in" : "Create workspace"}</Button>
          </form>
          {API_MOCK && <div className="mt-4"><Notice tone="info" title="Demo mode">Running on built-in sample data. {isLogin ? "Any password works for the demo account." : "Nothing leaves this browser."}</Notice></div>}
          <p className="mt-6 text-sm text-muted">
            {isLogin ? "New here? " : "Already have an account? "}
            <Link href={isLogin ? "/signup" : "/login"} className="font-medium text-accent underline underline-offset-4">{isLogin ? "Create a workspace" : "Log in"}</Link>
          </p>
        </main>
      </div>
      <aside aria-hidden="true" className="dotgrid relative hidden overflow-hidden border-l border-line bg-surface-2 lg:block">
        <div className="absolute inset-0 grid place-items-center p-12">
          <div className="max-w-md">
            <p className="display text-5xl leading-[1.05]">Every claim points to a line of code.</p>
            <div className="quote mt-8 px-4 py-3"><span className="eyebrow block">src/main/java/.../Owner.java:57</span><code className="font-mono text-[12.5px]">@Pattern(regexp = &quot;\\d{"{10}"}&quot;)</code></div>
          </div>
        </div>
      </aside>
    </div>
  );
}
