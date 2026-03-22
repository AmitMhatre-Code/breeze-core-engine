"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PrefilledOrderCard } from "@/components/order/PrefilledOrderCard";

function Inner() {
  const sp = useSearchParams();
  const q = sp.toString();
  return (
    <AppShell>
      <Suspense fallback={null}>
        <PrefilledOrderCard />
      </Suspense>
      <section className="app-card p-4 text-sm text-zinc-700 dark:text-zinc-300">
        <h2 className="app-text-heading">Vertical spread</h2>
        <p className="mt-2 app-text-muted">
          Query-driven workflow. JSON API:{" "}
          <code className="text-zinc-600 dark:text-zinc-400">/vertical-spread/data</code>.
        </p>
        {q && <pre className="app-pre mt-4 max-h-40">{q}</pre>}
        <Link href="/strategy-builder" className="app-link mt-4 inline-block text-xs">
          Back to Strategy Builder
        </Link>
      </section>
    </AppShell>
  );
}

export default function VerticalSpreadPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-background p-8 text-zinc-500 dark:text-zinc-400">
          Loading…
        </div>
      }
    >
      <Inner />
    </Suspense>
  );
}
