"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MarkdownContent } from "@/components/markdown/MarkdownContent";
import { HelpLink } from "@/components/help/HelpLink";
import { Modal } from "@/components/ui/Modal";

const SCROLL_THRESHOLD_PX = 32;

type Props = {
  open: boolean;
  pending: boolean;
  contentMarkdown: string;
  version: number | null;
  effectiveDate: string | null;
  onProceed: () => void;
};

function useScrollReachedBottom(contentKey: string, open: boolean) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [hasReachedBottom, setHasReachedBottom] = useState(false);

  const checkScrollPosition = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_THRESHOLD_PX;
    setHasReachedBottom(atBottom);
  }, []);

  useEffect(() => {
    if (!open) return;
    setHasReachedBottom(false);
    let innerFrame = 0;
    const outerFrame = requestAnimationFrame(() => {
      innerFrame = requestAnimationFrame(() => {
        checkScrollPosition();
      });
    });
    return () => {
      cancelAnimationFrame(outerFrame);
      cancelAnimationFrame(innerFrame);
    };
  }, [contentKey, open, checkScrollPosition]);

  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    if (!el) return;

    const observer = new ResizeObserver(() => {
      checkScrollPosition();
    });
    observer.observe(el);
    for (const child of el.children) {
      observer.observe(child);
    }
    return () => observer.disconnect();
  }, [contentKey, open, checkScrollPosition]);

  return { scrollRef, hasReachedBottom, checkScrollPosition };
}

export function LoginDisclosureDialog({
  open,
  pending,
  contentMarkdown,
  version,
  effectiveDate,
  onProceed,
}: Props) {
  const { scrollRef, hasReachedBottom, checkScrollPosition } = useScrollReachedBottom(
    contentMarkdown,
    open,
  );

  return (
    <Modal
      open={open}
      onClose={() => {}}
      dismissible={false}
      pending={pending}
      titleId="login-disclosure-title"
      descriptionId="login-disclosure-scroll-hint"
      zIndexClass="z-[200]"
      panelClassName="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl border border-zinc-700 bg-zinc-950 shadow-2xl"
    >
      <header className="shrink-0 border-b border-zinc-800 px-5 py-4">
        <p className="text-body font-semibold uppercase tracking-[0.2em] text-blue-400">
          Required
        </p>
        <h2
          id="login-disclosure-title"
          className="text-lg font-bold tracking-tight text-zinc-50"
        >
          SEBI Risk Disclosure
        </h2>
        {version != null ? (
          <p className="mt-1 text-xs text-zinc-400">
            Version {version}
            {effectiveDate ? ` · Effective ${effectiveDate}` : null}
          </p>
        ) : null}
      </header>

      <div
        ref={scrollRef}
        onScroll={checkScrollPosition}
        className="min-h-0 flex-1 overflow-y-auto px-5 py-4"
      >
        <MarkdownContent
          markdown={contentMarkdown}
          className="text-sm leading-relaxed text-zinc-300"
        />
      </div>

      <footer className="shrink-0 border-t border-zinc-800 px-5 py-4">
        <p
          id="login-disclosure-scroll-hint"
          className="mb-3 text-xs leading-relaxed text-zinc-400"
        >
          You must read and acknowledge the risk disclosure before using Breeze Modern.{" "}
          <HelpLink topicId="risk-disclosure-login" className="text-zinc-400">
            Why every login?
          </HelpLink>
        </p>
        <div className="flex flex-col items-end gap-2">
          <button
            type="button"
            disabled={pending || !hasReachedBottom}
            onClick={onProceed}
            className="app-btn-primary min-w-[10rem] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "Saving…" : "Proceed"}
          </button>
          {!hasReachedBottom ? (
            <p className="max-w-xs text-right text-xs leading-relaxed text-zinc-400">
              Scroll to the bottom of the message to enable Proceed.
            </p>
          ) : null}
        </div>
      </footer>
    </Modal>
  );
}
