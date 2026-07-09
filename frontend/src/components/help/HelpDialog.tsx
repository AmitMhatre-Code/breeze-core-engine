"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { HelpTopicBody } from "@/components/help/HelpTopicBody";
import { Modal } from "@/components/ui/Modal";
import { HELP_CATEGORY_LABELS, HELP_CATEGORY_ORDER } from "@/lib/help/categories";
import type { HelpTab } from "@/lib/help/help-context";
import { filterHelpTopics } from "@/lib/help/search-topics";
import { helpTopics } from "@/lib/help/topics";
import { getSalesEmail } from "@/lib/contact-sales-mailto";
import { LICENSE_CONSOLE_URL } from "@/lib/deployment-license";

const NAV_ROWS = [
  { keys: "Alt + 1", action: "Go to Dashboard" },
  { keys: "Alt + 2", action: "Go to Portfolio" },
  { keys: "Alt + 3", action: "Go to Performance" },
  { keys: "Alt + 4", action: "Go to Order Book" },
  { keys: "Alt + 5", action: "Go to Place Order" },
  { keys: "Alt + 6", action: "Go to Basket Order" },
  { keys: "Alt + 7", action: "Go to Strategy Builder" },
  { keys: "Alt + 8", action: "Go to Settings" },
] as const;

const CONTEXT_ROWS = [
  { keys: "/", action: "Focus scrip search (trading pages)" },
  { keys: "?", action: "Open help" },
  { keys: "Escape", action: "Close the topmost dialog or menu" },
] as const;

export function HelpDialog({
  open,
  onClose,
  activeTopicId,
  initialTab = "topics",
}: {
  open: boolean;
  onClose: () => void;
  activeTopicId: string | null;
  initialTab?: HelpTab;
}) {
  const titleId = useId();
  const searchId = useId();
  const [tab, setTab] = useState<HelpTab>(initialTab);
  const [query, setQuery] = useState("");
  const topicRefs = useRef<Record<string, HTMLDetailsElement | null>>({});

  useEffect(() => {
    if (open) {
      setTab(initialTab);
      setQuery("");
    }
  }, [open, initialTab]);

  useEffect(() => {
    if (!open || !activeTopicId) return;
    const el = topicRefs.current[activeTopicId];
    if (el) {
      el.open = true;
      requestAnimationFrame(() => {
        el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    }
  }, [open, activeTopicId, query]);

  const filtered = useMemo(
    () => filterHelpTopics(query, helpTopics),
    [query],
  );

  const grouped = useMemo(() => {
    const byCat = new Map<string, typeof filtered>();
    for (const t of filtered) {
      const list = byCat.get(t.category) ?? [];
      list.push(t);
      byCat.set(t.category, list);
    }
    return HELP_CATEGORY_ORDER.filter((c) => byCat.has(c)).map((category) => ({
      category,
      label: HELP_CATEGORY_LABELS[category],
      topics: byCat.get(category)!,
    }));
  }, [filtered]);

  const salesEmail = getSalesEmail();

  return (
    <Modal
      open={open}
      onClose={onClose}
      variant="bottomSheet"
      titleId={titleId}
    >
      <div className="sticky top-0 z-[1] border-b border-zinc-100 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950 sm:px-5 sm:py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2
              id={titleId}
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              Help
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Topics and keyboard shortcuts. Press{" "}
              <kbd className="rounded border border-zinc-200 bg-zinc-50 px-1 py-0.5 font-mono text-body dark:border-zinc-700 dark:bg-zinc-900">
                ?
              </kbd>{" "}
              anytime.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-md text-zinc-700 transition hover:bg-zinc-100 sm:size-10 dark:text-zinc-200 dark:hover:bg-zinc-900"
            aria-label="Close"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden
            >
              <path d="M18 6 6 18M6 6l12 12" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div
          className="mt-3 flex gap-1 rounded-lg border border-zinc-200 bg-zinc-50 p-0.5 dark:border-zinc-700 dark:bg-zinc-900"
          role="tablist"
          aria-label="Help sections"
        >
          {(
            [
              ["topics", "Topics"],
              ["shortcuts", "Shortcuts"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className={[
                "flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition",
                tab === id
                  ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100"
                  : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200",
              ].join(" ")}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-5 sm:pb-4">
        {tab === "topics" ? (
          <div className="space-y-4" role="tabpanel">
            <label htmlFor={searchId} className="sr-only">
              Search help topics
            </label>
            <input
              id={searchId}
              type="search"
              placeholder="Search topics…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
            />

            {grouped.length === 0 ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                No topics match your search.
              </p>
            ) : (
              <div className="space-y-4">
                {grouped.map((group) => (
                  <section key={group.category}>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                      {group.label}
                    </h3>
                    <div className="space-y-2">
                      {group.topics.map((topic) => (
                        <details
                          key={topic.id}
                          ref={(el) => {
                            topicRefs.current[topic.id] = el;
                          }}
                          open={activeTopicId === topic.id}
                          className="group rounded-md border border-zinc-200 dark:border-zinc-800"
                        >
                          <summary className="cursor-pointer list-none px-3 py-2.5 marker:hidden dark:text-zinc-200 [&::-webkit-details-marker]:hidden">
                            <span className="flex items-center justify-between gap-2">
                              <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
                                {topic.title}
                              </span>
                              <span
                                className="shrink-0 text-zinc-500 transition group-open:rotate-180 dark:text-zinc-400"
                                aria-hidden
                              >
                                <svg
                                  width="14"
                                  height="14"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                >
                                  <path d="m6 9 6 6 6-6" strokeLinecap="round" />
                                </svg>
                              </span>
                            </span>
                            <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                              {topic.summary}
                            </p>
                          </summary>
                          <div className="border-t border-zinc-100 px-3 pb-3 pt-2 dark:border-zinc-800">
                            <HelpTopicBody topic={topic} />
                          </div>
                        </details>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4 text-sm" role="tabpanel">
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Shortcuts are disabled while typing in a field.
            </p>
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Navigation
              </h3>
              <ul className="mt-2 space-y-1.5">
                {NAV_ROWS.map((row) => (
                  <li key={row.keys} className="flex justify-between gap-4">
                    <kbd className="shrink-0 rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 font-mono text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200">
                      {row.keys}
                    </kbd>
                    <span className="text-right text-zinc-700 dark:text-zinc-300">
                      {row.action}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Trading pages
              </h3>
              <ul className="mt-2 space-y-1.5">
                {CONTEXT_ROWS.map((row) => (
                  <li key={row.keys} className="flex justify-between gap-4">
                    <kbd className="shrink-0 rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 font-mono text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200">
                      {row.keys}
                    </kbd>
                    <span className="text-right text-zinc-700 dark:text-zinc-300">
                      {row.action}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}

        <footer className="mt-6 border-t border-zinc-100 pt-4 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <p className="flex flex-wrap gap-x-3 gap-y-1">
            <a
              href={LICENSE_CONSOLE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="app-link font-medium"
            >
              License console
            </a>
            {salesEmail ? (
              <a href={`mailto:${salesEmail}`} className="app-link font-medium">
                Contact sales
              </a>
            ) : null}
            <Link href="/terms-and-conditions" className="app-link font-medium">
              Terms &amp; Conditions
            </Link>
          </p>
        </footer>
      </div>
    </Modal>
  );
}
