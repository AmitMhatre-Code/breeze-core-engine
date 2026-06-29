"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { HelpLink } from "@/components/help/HelpLink";

const STORAGE_KEY = "setupChecklistDismissed:v1";

const STEPS = [
  {
    label: "Broker credentials",
    href: "/settings/credentials",
    topicId: "credentials",
  },
  {
    label: "Quantity limits",
    href: "/settings/quantity-limits",
    topicId: "quantity-limits",
  },
  {
    label: "Reference data loads",
    href: "/settings/reference-data-loads",
    topicId: "reference-data-loads",
  },
  {
    label: "Exchange calendar",
    href: "/settings/exchange-calendar",
    topicId: "exchange-calendar",
  },
] as const;

export function SetupChecklistCard({
  variant = "dashboard",
}: {
  variant?: "dashboard" | "settings";
}) {
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    try {
      setDismissed(sessionStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);

  const dismiss = useCallback(() => {
    setDismissed(true);
    try {
      sessionStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
  }, []);

  if (dismissed) return null;

  const intro =
    variant === "dashboard"
      ? "Complete these steps before your first trade."
      : "Recommended configuration for a new deployment.";

  return (
    <section
      className="rounded-lg border border-sky-200/80 bg-sky-50/60 p-4 dark:border-sky-900/45 dark:bg-sky-950/25"
      aria-label="Setup checklist"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            Setup checklist
          </h2>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            {intro}{" "}
            <HelpLink topicId="setup-checklist" className="inline text-sm">
              Full guide
            </HelpLink>
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
          aria-label="Dismiss setup checklist"
        >
          Dismiss
        </button>
      </div>
      <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-sm text-zinc-700 dark:text-zinc-300">
        {STEPS.map((step, i) => (
          <li key={step.href}>
            <Link href={step.href} className="app-link font-medium">
              {step.label}
            </Link>
            <span className="text-zinc-500 dark:text-zinc-400">
              {" "}
              —{" "}
              <HelpLink topicId={step.topicId} className="inline text-sm">
                why
              </HelpLink>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
