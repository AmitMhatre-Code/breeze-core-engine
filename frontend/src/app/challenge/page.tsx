"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { fetchAuthSession } from "@/lib/auth-session";
import {
  preloadLoginDisclosure,
  preloadLoginDisclosureAuthed,
} from "@/lib/login-disclosure-preload";
import { clearSessionAck, markDisclosurePending } from "@/lib/login-disclosure-session";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";

type ChallengeCtx = { user_id: string | null };

function ChallengeForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const apisession = searchParams.get("apisession") ?? "";
  const [secret, setSecret] = useState("");
  const [ctx, setCtx] = useState<ChallengeCtx | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!apisession) return;
    void preloadLoginDisclosure();
  }, [apisession]);

  useEffect(() => {
    let cancelled = false;
    void apiClient
      .get<ChallengeCtx>("/auth/challenge-context")
      .then((c) => {
        if (!cancelled) setCtx(c);
      })
      .catch(() => {
        if (!cancelled) setCtx({ user_id: null });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const userId = ctx?.user_id ?? "";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setSubmitting(true);
    try {
      const res = await apiClient.post<{ redirect: string }>(
        "/auth/icici-session",
        {
          user_id: userId,
          apisession,
          secret_user: secret,
          action: "Submit",
        },
      );
      clearSessionAck();
      markDisclosurePending();
      try {
        await queryClient.prefetchQuery({
          queryKey: ["auth", "session"],
          queryFn: fetchAuthSession,
          staleTime: 30_000,
        });
        await preloadLoginDisclosureAuthed(queryClient);
      } catch {
        // Redirect anyway; LoginDisclosureProvider will retry the fetches.
      }
      router.replace(res.redirect || "/dashboard");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Sign-in failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        Loading…
      </div>
    );
  }

  if (!apisession) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))] text-zinc-100">
        <div className="max-w-md rounded-lg border border-zinc-800 bg-zinc-900/80 p-6 text-sm">
          <p className="text-zinc-300">Missing session token in URL.</p>
          <Link href="/login" className="app-link mt-4 inline-block">
            Back to login
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))] text-zinc-50">
      <div className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-900/80 p-8 shadow-xl">
        <h1 className="text-xl font-semibold">Complete ICICI login</h1>
        <p className="mt-1 text-xs text-zinc-400">Step 2 of 2</p>
        <p className="mt-4 text-sm text-zinc-400">
          User id{" "}
          <span className="font-medium text-zinc-200">{userId || "—"}</span>
        </p>
        {err && (
          <p className="mt-4 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-sm text-red-200">
            {err}
          </p>
        )}
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <label className="block text-sm text-zinc-300">
            Challenge fragment (the part you memorized)
            <input
              required
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoComplete="off"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || !userId}
            aria-busy={submitting}
            className="app-btn-primary w-full py-2.5"
          >
            <AsyncLabelSpan
              busy={submitting}
              idleLabel="Submit"
              busyLabel="Signing in…"
            />
          </button>
        </form>
        <div className="mt-6 space-y-3 text-center text-heading text-zinc-400">
          <Link href="/login" className="app-link">
            Back to login
          </Link>
          <p>
            Wrong credentials?{" "}
            <Link href="/register/correct" className="app-link">
              Update credentials
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export default function ChallengePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
          Loading…
        </div>
      }
    >
      <ChallengeForm />
    </Suspense>
  );
}
