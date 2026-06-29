"use client";

import type { HelpTopic } from "@/lib/help/topics";
import { HelpLink } from "@/components/help/HelpLink";

export function HelpTopicBody({
  topic,
  showRelated = true,
}: {
  topic: HelpTopic;
  showRelated?: boolean;
}) {
  return (
    <div className="space-y-2 text-sm text-zinc-700 dark:text-zinc-300">
      <p className="text-xs text-zinc-500 dark:text-zinc-400">{topic.summary}</p>
      {topic.body.map((paragraph, i) => (
        <p key={i} className="leading-relaxed">
          {paragraph}
        </p>
      ))}
      {showRelated && topic.relatedTopicIds && topic.relatedTopicIds.length > 0 ? (
        <div className="border-t border-zinc-100 pt-2 dark:border-zinc-800">
          <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            Related
          </p>
          <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
            {topic.relatedTopicIds.map((id) => (
              <li key={id}>
                <HelpLink topicId={id} className="text-xs" />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
