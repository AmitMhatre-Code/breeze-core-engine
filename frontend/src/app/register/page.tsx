"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";

type Session = {
  google_authenticated: boolean;
  direct_registration_available?: boolean;
};

type Flow = "google" | "direct";

export default function RegisterPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [flow, setFlow] = useState<Flow>("google");
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
      .catch(() => setSession({ google_authenticated: false }));
  }, []);

  async function onSubmitGoogle(e: React.FormEvent) {
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
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        Loading…
      </div>
    );
  }

  const showDirect =
    session.direct_registration_available !== false;

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 py-10 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] pb-[max(2.5rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))] text-zinc-50">
      <div className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-900/80 p-8">
        <h1 className="text-xl font-semibold">Register</h1>
        <p className="mt-2 text-xs text-zinc-400">
          Link your ICICI Breeze API credentials and choose how you sign in.
        </p>

        {showDirect && (
          <div className="mt-4 flex rounded-lg border border-zinc-700 p-0.5 text-xs">
            <button
              type="button"
              className={`flex-1 rounded-md py-2 font-medium ${
                flow === "google"
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
              onClick={() => {
                setFlow("google");
                setErr(null);
              }}
            >
              With Google
            </button>
            <button
              type="button"
              className={`flex-1 rounded-md py-2 font-medium ${
                flow === "direct"
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
              onClick={() => {
                setFlow("direct");
                setErr(null);
              }}
            >
              App password
            </button>
          </div>
        )}

        {flow === "google" && !session.google_authenticated && (
          <div className="mt-6">
            <p className="text-xs text-zinc-400">
              Sign in with Google, then enter your ICICI API credentials.
            </p>
            <div className="mt-4">
              <GoogleSignInButton href="/auth/google?next=/register" />
            </div>
          </div>
        )}

        {flow === "google" && session.google_authenticated && (
          <>
            <h2 className="mt-6 text-sm font-medium text-zinc-200">
              ICICI Breeze credentials
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              ICICI Direct user id and the API key and secret fragment from Breeze API registration.
            </p>
            {err && (
              <p className="mt-4 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-sm text-red-200">
                {err}
              </p>
            )}
            <form onSubmit={onSubmitGoogle} className="mt-6 space-y-3">
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
                aria-busy={busy}
                className="app-btn-primary w-full py-2.5"
              >
                <AsyncLabelSpan
                  busy={busy}
                  idleLabel="Complete registration"
                  busyLabel="Saving…"
                />
              </button>
            </form>
          </>
        )}

        {flow === "direct" && showDirect && (
          <>
            <h2 className="mt-6 text-sm font-medium text-zinc-200">
              Direct registration
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              Your ICICI user id is your app username. Choose an app-only password (min 8
              characters), plus API key and secret fragment.
            </p>
            {err && (
              <p className="mt-4 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-sm text-red-200">
                {err}
              </p>
            )}
            <form onSubmit={onSubmitDirect} className="mt-6 space-y-3">
              <input
                required
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                placeholder="ICICI user id"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                autoComplete="username"
              />
              <input
                required
                minLength={8}
                type="password"
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                placeholder="App password (min 8 characters)"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
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
                aria-busy={busy}
                className="app-btn-primary w-full py-2.5"
              >
                <AsyncLabelSpan
                  busy={busy}
                  idleLabel="Create account"
                  busyLabel="Saving…"
                />
              </button>
            </form>
          </>
        )}

        <p className="mt-6 text-center text-xs text-zinc-500">
          <Link href="/login" className="app-link">
            Back to login
          </Link>
        </p>
      </div>
    </div>
  );
}
