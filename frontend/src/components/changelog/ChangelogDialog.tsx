"use client";

import { formatAppVersionForDisplay } from "@/lib/app-version";
import { Modal } from "@/components/ui/Modal";
import {
  getHistoryReleases,
  getLatestFeatureRelease,
  getLatestRelease,
  type ChangelogRelease,
  type ReleaseKind,
} from "@/lib/changelog";

export type ChangelogDialogProps = {
  open: boolean;
  onClose: () => void;
};

const KIND_BADGE: Record<
  ReleaseKind,
  { label: string; className: string }
> = {
  major: {
    label: "Major",
    className:
      "bg-sky-100 text-sky-900 dark:bg-sky-950/70 dark:text-sky-200",
  },
  minor: {
    label: "Minor",
    className:
      "bg-slate-100 text-slate-900 dark:bg-slate-900/70 dark:text-slate-200",
  },
  patch: {
    label: "Patch",
    className:
      "bg-zinc-200/90 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200",
  },
  prerelease: {
    label: "Pre-release",
    className:
      "bg-amber-100 text-amber-950 dark:bg-amber-950/50 dark:text-amber-200",
  },
};

function KindBadge({ kind }: { kind: ReleaseKind }) {
  const badge = KIND_BADGE[kind];
  return (
    <span
      className={[
        "inline-flex shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        badge.className,
      ].join(" ")}
    >
      {badge.label}
    </span>
  );
}

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
  return `${formatAppVersionForDisplay(release.version)} · ${release.date}`;
}

function isFeatureReleaseKind(kind: ReleaseKind) {
  return kind === "major" || kind === "minor";
}

function FeatureReleasePanel({ release }: { release: ChangelogRelease }) {
  return (
    <section
      className="rounded-lg border border-sky-200/90 bg-sky-50/80 p-4 dark:border-sky-900/45 dark:bg-sky-950/30"
      aria-label="Feature release"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-sky-800 dark:text-sky-300">
          Feature release
        </span>
        <KindBadge kind={release.releaseKind} />
      </div>
      <div className="mt-2 text-base font-semibold text-zinc-900 dark:text-zinc-100">
        {formatReleaseHeading(release)}
      </div>
      {release.summary ? (
        <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
          {release.summary}
        </p>
      ) : null}
      <ReleaseBullets release={release} />
    </section>
  );
}

function LatestBuildPanel({ release }: { release: ChangelogRelease }) {
  return (
    <section
      className="rounded-md border border-zinc-200 bg-zinc-50/80 p-3 dark:border-zinc-700 dark:bg-zinc-900/35"
      aria-label="Latest update"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-600 dark:text-zinc-400">
          Latest update (your build)
        </span>
        <KindBadge kind={release.releaseKind} />
      </div>
      <div className="mt-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        {formatReleaseHeading(release)}
      </div>
      {release.summary ? (
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          {release.summary}
        </p>
      ) : null}
      <ReleaseBullets release={release} />
    </section>
  );
}

function UnifiedLatestPanel({ release }: { release: ChangelogRelease }) {
  const isFeature = isFeatureReleaseKind(release.releaseKind);
  return (
    <section
      className={
        isFeature
          ? "rounded-lg border border-sky-200/90 bg-sky-50/80 p-4 dark:border-sky-900/45 dark:bg-sky-950/30"
          : "rounded-md border border-zinc-200 bg-zinc-50/80 p-4 dark:border-zinc-700 dark:bg-zinc-900/35"
      }
      aria-label="Latest release"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={
            isFeature
              ? "text-xs font-medium uppercase tracking-wide text-sky-800 dark:text-sky-300"
              : "text-xs font-medium uppercase tracking-wide text-zinc-600 dark:text-zinc-400"
          }
        >
          {release.releaseKind === "major"
            ? "Latest major release"
            : release.releaseKind === "minor"
              ? "Latest feature release"
              : "Latest release"}
        </span>
        <KindBadge kind={release.releaseKind} />
      </div>
      <div className="mt-2 text-base font-semibold text-zinc-900 dark:text-zinc-100">
        {formatReleaseHeading(release)}
      </div>
      {release.summary ? (
        <p
          className={
            isFeature
              ? "mt-1 text-sm text-zinc-700 dark:text-zinc-300"
              : "mt-1 text-sm text-zinc-600 dark:text-zinc-400"
          }
        >
          {release.summary}
        </p>
      ) : null}
      <ReleaseBullets release={release} />
    </section>
  );
}

export function ChangelogDialog({ open, onClose }: ChangelogDialogProps) {
  const latest = getLatestRelease();
  const latestFeature = getLatestFeatureRelease();

  const showSplitLayout = Boolean(
    latest &&
      (latest.releaseKind === "patch" || latest.releaseKind === "prerelease") &&
      latestFeature &&
      (latestFeature.version !== latest.version ||
        latestFeature.date !== latest.date),
  );

  const history = getHistoryReleases(
    latest,
    showSplitLayout ? latestFeature : undefined,
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      variant="bottomSheet"
      titleId="changelog-dialog-title"
    >
      <div className="sticky top-0 z-[1] flex items-start justify-between gap-3 border-b border-zinc-100 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950 sm:px-5 sm:py-4">
          <div className="min-w-0">
            <h2
              id="changelog-dialog-title"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              What&apos;s new
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Feature releases are highlighted; patch and pre-release builds are
              listed so you can match your app version.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-md text-zinc-700 transition-all hover:bg-zinc-100 hover:text-zinc-900 sm:size-10 dark:text-zinc-200 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
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

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-4 py-4 pe-[max(1rem,env(safe-area-inset-right))] pb-[max(1rem,env(safe-area-inset-bottom))] ps-[max(1rem,env(safe-area-inset-left))] sm:px-5 sm:pe-5 sm:ps-5 sm:pb-4">
          {!latest ? (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              No release notes yet.
            </p>
          ) : showSplitLayout && latestFeature ? (
            <>
              <FeatureReleasePanel release={latestFeature} />
              <LatestBuildPanel release={latest} />
              {history.length > 0 ? (
                <HistorySection releases={history} />
              ) : null}
            </>
          ) : (
            <>
              <UnifiedLatestPanel release={latest} />
              {history.length > 0 ? (
                <HistorySection releases={history} />
              ) : null}
            </>
          )}
        </div>
    </Modal>
  );
}

function HistorySection({ releases }: { releases: ChangelogRelease[] }) {
  return (
    <section className="border-t border-zinc-100 pt-4 dark:border-zinc-800">
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Earlier releases
      </div>
      <div className="space-y-2">
        {releases.map((release) => (
          <details
            key={`${release.version}-${release.date}`}
            className="group rounded-md border border-zinc-200 dark:border-zinc-800"
          >
            <summary className="cursor-pointer list-none px-3 py-2 marker:hidden dark:text-zinc-200 [&::-webkit-details-marker]:hidden">
              <span className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
                    {formatReleaseHeading(release)}
                  </span>
                  <KindBadge kind={release.releaseKind} />
                </span>
                <span
                  className="shrink-0 text-zinc-400 transition group-open:rotate-180 dark:text-zinc-500"
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
  );
}
