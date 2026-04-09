import type { ReactNode } from "react";
import {
  strategySetupTooltipLines,
  type StrategyCardId,
} from "@/lib/strategy-builder/templates";

type Props = {
  strategyId: StrategyCardId;
  children: ReactNode;
};

export function ReadymadeSetupTooltip({ strategyId, children }: Props) {
  const lines = strategySetupTooltipLines(strategyId);
  const text = lines.join("\n");

  return (
    <div className="group relative inline-block">
      {children}
      {lines.length > 0 ? (
        <div
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 hidden max-w-[15rem] min-w-[11rem] -translate-x-1/2 rounded-lg bg-zinc-950/95 px-2.5 py-2 text-left text-[10px] font-medium leading-snug text-white shadow-lg whitespace-pre-line group-hover:block dark:bg-zinc-950/95"
        >
          {text}
        </div>
      ) : null}
    </div>
  );
}
