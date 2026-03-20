"use client";

import { ThemeToggle } from "@/components/theme/ThemeToggle";

export default function Home() {
  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-xl rounded-2xl border border-zinc-200 bg-white/90 p-8 shadow-lg dark:border-zinc-800 dark:bg-zinc-900/60 dark:shadow-xl dark:shadow-black/40">
        <div className="mb-6 space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            ICICI Breeze Modern
          </h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Options trading dashboard inspired by Sensibull, running locally
            with your existing ICICI integration.
          </p>
        </div>
        <div className="space-y-4">
          <a
            href="/login"
            className="inline-flex w-full items-center justify-center rounded-xl bg-emerald-500 px-4 py-2.5 text-sm font-medium text-black shadow-sm transition hover:bg-emerald-400 dark:bg-emerald-500"
          >
            Continue to login
          </a>
          <p className="text-xs text-zinc-500">
            You will be asked to authenticate with Google and ICICI before
            accessing your dashboard, portfolio, and strategies.
          </p>
        </div>
      </div>
    </div>
  );
}
