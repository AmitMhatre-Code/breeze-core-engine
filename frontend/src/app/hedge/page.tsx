"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { AppShell } from "@/components/layout/AppShell";

function HedgeInner() {
  const sp = useSearchParams();
  const q = sp.toString();
  return (
    <AppShell>
      <section className="app-card p-4 text-sm text-zinc-700 dark:text-zinc-300">
        <h2 className="app-text-heading">Hedge workspace</h2>
        <p className="mt-2 app-text-muted">
          Strategy parameters are passed in the URL query string (legacy deep links).
          Full interactive tooling can extend this page; data APIs live under{" "}
          <code className="text-zinc-600 dark:text-zinc-400">/hedge/data</code>.
        </p>
        {q && (
          <pre className="app-pre mt-4 max-h-40">{q}</pre>
        )}
        <Link href="/strategies" className="app-link mt-4 inline-block text-xs">
          Back to strategies
        </Link>
      </section>
    </AppShell>
  );
}

export default function HedgePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-background p-8 text-zinc-500 dark:text-zinc-400">
          Loading…
        </div>
      }
    >
      <HedgeInner />
    </Suspense>
  );
}
