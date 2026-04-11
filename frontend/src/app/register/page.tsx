"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";

type Session = {
  direct_registration_available?: boolean;
};

export default function RegisterPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void apiClient
      .get<Session>("/api/register/session")
      .then(setSession)
      .catch(() => setSession({ direct_registration_available: true }));
  }, []);

  async function onSubmitDirect(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>(
        "/api/register/direct",
        {
          user_id: userId,
          password,
          api_key: apiKey,
          secret_fragment: secret,
        },
      );
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  if (!session) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background text-zinc-500 dark:text-zinc-400">
        Loading…
      </div>
    );
  }

  const showDirect = session.direct_registration_available !== false;

  if (!showDirect) {
    return (
      <div className="relative flex min-h-screen items-center justify-center bg-background px-4 py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">
        <div className="absolute end-[max(1rem,env(safe-area-inset-right))] top-[max(1rem,env(safe-area-inset-top))]">
          <ThemeToggle />
        </div>
        Registration is not available.
        <Link href="/login" className="app-link ml-2">
          Back to login
        </Link>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background py-10 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] pb-[max(2.5rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))] text-foreground">
      <div className="absolute end-[max(1rem,env(safe-area-inset-right))] top-[max(1rem,env(safe-area-inset-top))]">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-md rounded-lg border border-zinc-200 bg-white/90 p-8 shadow-lg dark:border-zinc-800 dark:bg-zinc-900/80 dark:shadow-xl dark:shadow-black/40">
        <h1 className="text-xl font-semibold tracking-tight">Register</h1>
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          Link your ICICI Breeze API credentials and choose an app-only password for Breeze Web.
        </p>
        <h2 className="app-text-heading mt-6">Create account</h2>
        <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
          Your ICICI user id is your app username. Choose an app-only password (min 8 characters),
          plus API key and secret fragment from Breeze API registration.
        </p>
        {err && (
          <p className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-200">
            {err}
          </p>
        )}
        <form onSubmit={onSubmitDirect} className="mt-6 space-y-3">
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="ICICI user id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            autoComplete="username"
          />
          <input
            required
            minLength={8}
            type="password"
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="App password (min 8 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="API key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="Secret fragment (stored part)"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={busy}
            aria-busy={busy}
            className="app-btn-primary w-full py-2.5"
          >
            <AsyncLabelSpan busy={busy} idleLabel="Create account" busyLabel="Saving…" />
          </button>
        </form>
        <p className="mt-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
          <Link href="/login" className="app-link">
            Back to login
          </Link>
        </p>
      </div>
    </div>
  );
}
