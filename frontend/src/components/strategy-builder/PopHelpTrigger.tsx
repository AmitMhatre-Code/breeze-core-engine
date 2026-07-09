"use client";

import { InfoPopover } from "@/components/ui/InfoPopover";
import {
  POP_HELP_DEFINITION,
  POP_HELP_DIRECTIONAL,
  POP_HELP_DISCLAIMER,
  POP_HELP_INCOME,
  POP_HELP_TITLE,
} from "@/lib/strategy-builder/pop-help";

export function PopHelpTrigger() {
  return (
    <InfoPopover
      title={POP_HELP_TITLE}
      ariaLabel="What is probability of profit?"
      learnMoreTopicId="probability-of-profit"
    >
      <div className="space-y-2">
        <p>{POP_HELP_DEFINITION}</p>
        <div>
          <p className="font-semibold text-foreground">Income strategies</p>
          <p className="mt-0.5">{POP_HELP_INCOME}</p>
        </div>
        <div>
          <p className="font-semibold text-foreground">
            Directional strategies
          </p>
          <p className="mt-0.5">{POP_HELP_DIRECTIONAL}</p>
        </div>
        <p className="text-muted">{POP_HELP_DISCLAIMER}</p>
      </div>
    </InfoPopover>
  );
}
