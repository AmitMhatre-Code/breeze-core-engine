"use client";

import { useEffect } from "react";
import {
  getLatestRelease,
  getOlderReleases,
  type ChangelogRelease,
} from "@/lib/changelog";

export type ChangelogDialogProps = {
  open: boolean;
  onClose: () => void;
};

function ReleaseBullets({ release }: { release: ChangelogRelease }) {
  return (
    <ul className="mt-2 list-inside list-disc space-y-1.5 text-sm text-zinc-700 dark:text-zinc-300">
      {release.changes.map((line, i) => (
        <li key={i} className="[text-indent:-0.35em] pl-1">
          {line}
        </li>
      ))}
    </ul>
  );
}

function formatReleaseHeading(release: ChangelogRelease) {
  const d = release.date;
  return `${release.version} · ${d}`;
}

export function ChangelogDialog({ open, onClose }: ChangelogDialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const latest = getLatestRelease();
  const older = getOlderReleases();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="changelog-dialog-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/50"
        aria-label="Close dialog"
        onClick={onClose}
      />
      <div className="relative max-h-[min(32rem,85vh)] w-full max-w-lg overflow-y-auto rounded-lg border border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="sticky top-0 z-[1] flex items-start justify-between gap-3 border-b border-zinc-100 bg-white px-5 py-4 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="min-w-0">
            <h2
              id="changelog-dialog-title"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              What&apos;s new
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Recent updates to Breeze Web
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center text-zinc-700 transition-all hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
            aria-label="Close"
            title="Close"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M18 6 6 18" />
              <path d="M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="space-y-5 px-5 py-4">
          {!latest ? (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              No release notes yet.
            </p>
          ) : (
            <>
              <section aria-label="Latest release">
                <div className="text-xs font-medium uppercase tracking-wide text-sky-700 dark:text-sky-400">
                  Latest
                </div>
                <div className="mt-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {formatReleaseHeading(latest)}
                </div>
                {latest.summary ? (
                  <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                    {latest.summary}
                  </p>
                ) : null}
                <ReleaseBullets release={latest} />
              </section>

              {older.length > 0 ? (
                <section className="border-t border-zinc-100 pt-4 dark:border-zinc-800">
                  <div className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Previous releases
                  </div>
                  <div className="space-y-2">
                    {older.map((release) => (
                      <details
                        key={`${release.version}-${release.date}`}
                        className="group rounded-md border border-zinc-200 dark:border-zinc-800"
                      >
                        <summary className="cursor-pointer list-none px-3 py-2 text-sm font-medium text-zinc-800 marker:hidden dark:text-zinc-200 [&::-webkit-details-marker]:hidden">
                          <span className="flex items-center justify-between gap-2">
                            <span>{formatReleaseHeading(release)}</span>
                            <span
                              className="text-zinc-400 transition group-open:rotate-180 dark:text-zinc-500"
                              aria-hidden
                            >
                              <svg
                                width="14"
                                height="14"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="m6 9 6 6 6-6" />
                              </svg>
                            </span>
                          </span>
                        </summary>
                        <div className="border-t border-zinc-100 px-3 pb-3 pt-2 dark:border-zinc-800">
                          {release.summary ? (
                            <p className="text-xs text-zinc-600 dark:text-zinc-400">
                              {release.summary}
                            </p>
                          ) : null}
                          <ReleaseBullets release={release} />
                        </div>
                      </details>
                    ))}
                  </div>
                </section>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
