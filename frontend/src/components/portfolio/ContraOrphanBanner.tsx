"use client";

import Link from "next/link";
import { useSquareOffRules } from "@/lib/portfolio/useSquareOffRules";
import { resetBannerMessage, resetHazardTier } from "@/lib/portfolio/reset-warning";

/**
 * App-level banner for a tier-3 Reset only: an exit order is still live but its position
 * is already closed, so filling would OPEN a brand-new position the user never asked for.
 *
 * This is a deliberate exception to how banners are used here. Every other one
 * (`LicenseStatusBanner`, `ApiLimitExhaustedBanner`) is deployment-wide, whereas this is
 * scoped to a single Strategy Group. It earns the escalation because a resting contra
 * order doesn't care which page you're on, and it can sit there for hours — arguably more
 * urgent than "API limit hit", which already gets a banner.
 *
 * Deliberately NOT shown for tier 2 (orders live but closing real legs): that state is
 * common enough that a persistent banner would train the user to ignore it, which would
 * blunt this one.
 *
 * `role="alert"` (assertive) rather than the house `role="status"` (polite) — this is a
 * live financial risk, not a status note.
 */
export function ContraOrphanBanner() {
  const rules = useSquareOffRules();
  const contra = (rules ?? []).filter(
    (r) => r.status === "reset" && resetHazardTier(r) === "contra_risk",
  );
  if (contra.length === 0) return null;

  const rule = contra[0];
  const message = resetBannerMessage(rule);
  if (!message) return null;

  return (
    <div
      role="alert"
      className="border-b border-down/40 bg-down-tint px-4 py-2 text-center text-sm text-down-on-tint"
    >
      <div className="mx-auto flex max-w-[1280px] flex-wrap items-center justify-center gap-x-2 gap-y-1">
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0"
          aria-hidden="true"
        >
          <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" />
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
        </svg>
        <span>
          {message}
          {contra.length > 1 ? ` (${contra.length} groups affected)` : ""}
        </span>
        <Link
          href="/orders"
          className="font-semibold underline underline-offset-2 hover:brightness-110"
        >
          Review
        </Link>
      </div>
    </div>
  );
}
