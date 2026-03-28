"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api-client";

type Session = {
  google_authenticated: boolean;
  has_account?: boolean | null;
  user_id?: string | null;
};

export default function RegisterCorrectPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void apiClient
      .get<Session>("/api/register/correct/session")
      .then(setSession)
      .catch(() => setSession({ google_authenticated: false }));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!session?.user_id) return;
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>(
        "/api/register/correct",
        {
          user_id: session.user_id,
          api_key: apiKey,
          secret_fragment: secret,
        },
      );
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
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
      <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 px-4 text-zinc-50">
        <button
          type="button"
          onClick={() => {
            window.location.href = "/auth/google?next=/register/correct";
          }}
          className="app-btn-primary px-6 py-2.5"
        >
          Sign in with Google
        </button>
        <Link href="/login" className="app-link mt-4 text-xs">
          Back to login
        </Link>
      </div>
    );
  }

  if (session.has_account === false) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 text-center text-sm text-zinc-400">
        No ICICI account linked to this Google user.
        <Link href="/register" className="app-link ml-2 text-sm">
          Register
        </Link>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 text-zinc-50">
      <div className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-900/80 p-8">
        <h1 className="text-xl font-semibold">Update credentials</h1>
        <p className="mt-1 text-xs text-zinc-500">User id: {session.user_id}</p>
        {err && (
          <p className="mt-4 rounded border border-red-900/50 bg-red-950/30 px-3 py-2 text-sm text-red-200">
            {err}
          </p>
        )}
        <form onSubmit={onSubmit} className="mt-6 space-y-3">
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
            placeholder="New secret fragment"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={busy}
            className="app-btn-primary w-full py-2.5"
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </form>
      </div>
    </div>
  );
}
