"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api-client";

type Session = { google_authenticated: boolean };

export default function RegisterPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [userId, setUserId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void apiClient
      .get<Session>("/api/register/session")
      .then(setSession)
      .catch(() => setSession({ google_authenticated: false }));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>("/api/register", {
        user_id: userId,
        api_key: apiKey,
        secret_fragment: secret,
      });
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  if (!session) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        Loading…
      </div>
    );
  }

  if (!session.google_authenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 text-zinc-50">
        <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/80 p-8">
          <h1 className="text-xl font-semibold">Register</h1>
          <p className="mt-2 text-xs text-zinc-400">
            Sign in with Google, then enter your ICICI API credentials.
          </p>
          <button
            type="button"
            onClick={() => {
              window.location.href = "/auth/google?next=/register";
            }}
            className="mt-6 w-full rounded-xl bg-emerald-500 py-2.5 text-sm font-medium text-black hover:bg-emerald-400"
          >
            Continue with Google
          </button>
          <p className="mt-4 text-center text-xs text-zinc-500">
            <Link href="/login" className="text-emerald-400 hover:underline">
              Back to login
            </Link>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 text-zinc-50">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/80 p-8">
        <h1 className="text-xl font-semibold">Broker credentials</h1>
        <p className="mt-1 text-xs text-zinc-400">
          ICICI user id, API key, and your memorized fragment.
        </p>
        {err && (
          <p className="mt-4 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-sm text-red-200">
            {err}
          </p>
        )}
        <form onSubmit={onSubmit} className="mt-6 space-y-3">
          <input
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            placeholder="ICICI user id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          />
          <input
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            placeholder="API key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <input
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            placeholder="Secret fragment (stored part)"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-emerald-500 py-2 text-sm font-medium text-black hover:bg-emerald-400 disabled:opacity-50"
          >
            {busy ? "Saving…" : "Complete registration"}
          </button>
        </form>
      </div>
    </div>
  );
}
