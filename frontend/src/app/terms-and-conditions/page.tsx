"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { MarkdownContent } from "@/components/markdown/MarkdownContent";
import { fetchTermsCurrent } from "@/lib/terms";

export default function TermsAndConditionsPage() {
  const termsQ = useQuery({
    queryKey: ["terms", "current"],
    queryFn: fetchTermsCurrent,
    staleTime: 60_000,
  });

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-50">
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
        <header className="mb-8 space-y-3 border-b border-zinc-800 pb-6">
          <p className="text-body font-semibold uppercase tracking-[0.2em] text-blue-400">
            Legal
          </p>
          <h1 className="text-2xl font-bold tracking-tight">Breeze Modern Terms &amp; Conditions</h1>
          {termsQ.data ? (
            <p className="text-sm text-zinc-400">
              Version {termsQ.data.version} · Effective {termsQ.data.effective_date}
            </p>
          ) : null}
          <Link href="/dashboard" className="inline-block text-sm text-blue-400 hover:text-blue-300">
            ← Back to dashboard
          </Link>
        </header>

        {termsQ.isLoading ? (
          <p className="text-sm text-zinc-400">Loading terms…</p>
        ) : termsQ.isError ? (
          <p className="text-sm text-red-400">
            {termsQ.error instanceof Error
              ? termsQ.error.message
              : "Could not load Terms & Conditions."}
          </p>
        ) : (
          <MarkdownContent
            markdown={termsQ.data?.content_markdown ?? ""}
            className="prose-invert text-sm leading-relaxed text-zinc-300"
          />
        )}
      </div>
    </div>
  );
}
