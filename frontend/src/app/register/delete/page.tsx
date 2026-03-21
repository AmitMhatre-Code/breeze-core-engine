"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api-client";

type Session = {
  google_authenticated: boolean;
  has_account?: boolean | null;
  user_id?: string | null;
};

export default function RegisterDeletePage() {
  const [session, setSession] = useState<Session | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void apiClient
      .get<Session>("/api/register/delete/session")
      .then(setSession)
      .catch(() => setSession({ google_authenticated: false }));
  }, []);

  async function onDelete() {
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>(
        "/api/register/delete",
        {},
      );
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
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
      <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 text-zinc-50">
        <button
          type="button"
          onClick={() => {
            window.location.href = "/auth/google?next=/register/delete";
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
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-sm text-zinc-400">
        Nothing to delete for this Google account.
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 text-zinc-50">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/80 p-8">
        <h1 className="text-xl font-semibold text-red-300">Delete registration</h1>
        <p className="mt-2 text-sm text-zinc-400">
          Remove stored credentials for{" "}
          <span className="text-zinc-200">{session.user_id}</span>.
        </p>
        {err && (
          <p className="mt-4 text-sm text-red-300">{err}</p>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={onDelete}
          className="mt-6 w-full rounded-xl border border-red-800 bg-red-950/40 py-2 text-sm font-medium text-red-200 hover:bg-red-950/60 disabled:opacity-50"
        >
          {busy ? "Working…" : "Confirm delete"}
        </button>
        <Link href="/settings" className="mt-4 block text-center text-xs text-zinc-500">
          Cancel
        </Link>
      </div>
    </div>
  );
}
