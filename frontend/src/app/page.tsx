"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiClient } from "@/lib/api-client";
import { isSessionExpiredRedirectPending } from "@/lib/auth-session-expired";
import { getIciciSessionFromSearchParams } from "@/lib/icici-session-query";

function HomeRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const searchKey = searchParams.toString();

  useEffect(() => {
    const token = getIciciSessionFromSearchParams(
      new URLSearchParams(searchKey),
    );
    if (token) {
      router.replace(
        `/challenge?apisession=${encodeURIComponent(token)}`,
      );
      return;
    }

    let cancelled = false;
    void apiClient
      .get<{ authenticated?: boolean }>("/auth/session")
      .then(() => {
        if (!cancelled) router.replace("/dashboard");
      })
      .catch(() => {
        if (!cancelled && !isSessionExpiredRedirectPending()) router.replace("/login");
      });

    return () => {
      cancelled = true;
    };
  }, [router, searchKey]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 text-sm text-zinc-500 dark:text-zinc-400">
      Loading…
    </div>
  );
}

export default function Home() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background text-zinc-500 dark:text-zinc-400">
          Loading…
        </div>
      }
    >
      <HomeRedirect />
    </Suspense>
  );
}
