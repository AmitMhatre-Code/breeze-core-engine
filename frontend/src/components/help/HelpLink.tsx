"use client";

import type { ReactNode } from "react";
import { getTopicById } from "@/lib/help/topics";
import { useHelpOptional } from "@/lib/help/help-context";

export function HelpLink({
  topicId,
  className = "",
  children,
}: {
  topicId: string;
  className?: string;
  children?: ReactNode;
}) {
  const help = useHelpOptional();
  const topic = getTopicById(topicId);
  const label = children ?? topic?.title ?? "Learn more";

  if (!help || !topic) {
    return (
      <span className={`text-zinc-500 ${className}`}>{label}</span>
    );
  }

  return (
    <button
      type="button"
      className={`app-link cursor-pointer border-0 bg-transparent p-0 text-left font-medium underline-offset-2 hover:underline ${className}`}
      onClick={() => help.openHelp(topicId)}
    >
      {label}
    </button>
  );
}
