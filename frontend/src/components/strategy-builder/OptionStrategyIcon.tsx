import type { ReactNode } from "react";
import type { TemplateId } from "@/lib/strategy-builder/templates";

type ReadymadeId = "build-your-own" | "naked-shorts" | "covered-shorts";

type Props = {
  templateId: TemplateId | ReadymadeId;
};

const ProfitColor = "#22c55e"; // green-500
const LossColor = "#ef4444"; // red-500

const Tile = ({ children }: { children: ReactNode }) => (
  <svg
    width="100%"
    height="100%"
    viewBox="0 0 62 62"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    color="primaryContainer"
    className="text-zinc-900 dark:text-zinc-50"
  >
    <rect
      className="background"
      width="60"
      height="60"
      transform="translate(1 1)"
      fill="currentColor"
      opacity="0.04"
    />
    <path d="M1.5 1V60.2366" strokeLinecap="square" stroke="currentColor" />
    <path
      d="M1.46154 31H61"
      strokeLinecap="round"
      strokeDasharray="5 5"
      stroke="currentColor"
    />
    {children}
  </svg>
);

export function OptionStrategyIcon({ templateId }: Props) {
  // Coordinates are normalized to a 62x62 viewBox:
  // - breakeven: y=31 (dashed)
  // - profit max: y=12 (green)
  // - loss max: y=52 (red)
  const x0 = 2;
  const xEnd = 60;
  const topY = 12;
  const midY = 31;
  const bottomY = 52;

  const strokeCommon = {
    strokeWidth: "2",
    strokeLinecap: "round" as const,
  };

  const line = (d: string, stroke: string) => (
    <path d={d} stroke={stroke} {...strokeCommon} />
  );

  // Layout conventions (x positions for the "strikes"):
  const K1_2 = 18;
  const K2_2 = 44;
  const mid_2 = 31; // midpoint between K1_2 and K2_2

  const K_1 = 31; // center strike for 1-strike strategies (straddle)
  const crossLeft = 16;
  const crossRight = 45;

  const K1_3 = 14;
  const K2_3 = 31;
  const K3_3 = 48;
  const crossUp = 23;
  const crossDown = 40;

  const K1_4 = 10;
  const K2_4 = 22;
  const K3_4 = 40;
  const K4_4 = 52;
  const crossUp4 = 16;
  const crossDown4 = 46;

  switch (templateId) {
    case "build-your-own":
      return (
        <svg
          width="80%"
          height="80%"
          viewBox="0 0 20 20"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="mx-auto block text-zinc-600 opacity-80 dark:text-zinc-300"
        >
          <path
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1"
            d="M7.75 4H19M7.75 4a2.25 2.25 0 0 1-4.5 0m4.5 0a2.25 2.25 0 0 0-4.5 0M1 4h2.25m13.5 6H19m-2.25 0a2.25 2.25 0 0 1-4.5 0m4.5 0a2.25 2.25 0 0 0-4.5 0M1 10h11.25m-4.5 6H19M7.75 16a2.25 2.25 0 0 1-4.5 0m4.5 0a2.25 2.25 0 0 0-4.5 0M1 16h2.25"
          />
        </svg>
      );

    case "naked-shorts":
      // Profit capped on one side, loss increases after breakeven.
      return (
        <Tile>
          {line(`M${x0} ${topY}H28`, ProfitColor)}
          {line(`M28 ${topY}L36 ${midY}`, ProfitColor)}
          {line(`M36 ${midY}L44 ${bottomY}`, LossColor)}
          {line(`M44 ${bottomY}H${xEnd}`, LossColor)}
        </Tile>
      );

    case "covered-shorts":
      // Profit in the middle, loss capped at extremes.
      return (
        <Tile>
          {line(`M${x0} ${bottomY}H10`, LossColor)}
          {line(`M10 ${bottomY}L16 ${midY}`, LossColor)}
          {line(`M16 ${midY}L22 ${topY}`, ProfitColor)}
          {line(`M22 ${topY}H40`, ProfitColor)}
          {line(`M40 ${topY}L46 ${midY}`, ProfitColor)}
          {line(`M46 ${midY}L52 ${bottomY}`, LossColor)}
          {line(`M52 ${bottomY}H${xEnd}`, LossColor)}
        </Tile>
      );

    case "bull_call_spread":
    {
      // Before K1: loss (bottom flat) -> Between: diagonal -> After K2: profit (top flat)
      return (
        <Tile>
          {line(`M${x0} ${bottomY}H${K1_2}`, LossColor)}
          {line(`M${K1_2} ${bottomY}L${mid_2} ${midY}`, LossColor)}
          {line(`M${mid_2} ${midY}L${K2_2} ${topY}`, ProfitColor)}
          {line(`M${K2_2} ${topY}H${xEnd}`, ProfitColor)}
        </Tile>
      );
    }

    case "bear_put_spread": {
      // Before K1: profit (top flat) -> Between: diagonal down -> After K2: loss (bottom flat)
      return (
        <Tile>
          {line(`M${x0} ${topY}H${K1_2}`, ProfitColor)}
          {line(`M${K1_2} ${topY}L${mid_2} ${midY}`, ProfitColor)}
          {line(`M${mid_2} ${midY}L${K2_2} ${bottomY}`, LossColor)}
          {line(`M${K2_2} ${bottomY}H${xEnd}`, LossColor)}
        </Tile>
      );
    }

    case "long_straddle": {
      // V-shape with max loss at strike, profit diagonals away from K1
      return (
        <Tile>
          {line(`M${x0} ${topY}L${crossLeft} ${midY}`, ProfitColor)}
          {line(`M${crossLeft} ${midY}L${K_1} ${bottomY}`, LossColor)}
          {line(`M${K_1} ${bottomY}L${crossRight} ${midY}`, LossColor)}
          {line(`M${crossRight} ${midY}L${xEnd} ${topY}`, ProfitColor)}
        </Tile>
      );
    }

    case "long_strangle": {
      // Bucket: diagonals down to trough, flat trough at max loss, then diagonals up
      const K1 = K1_2;
      const K2 = K2_2;
      const crossL = 10;
      const crossR = 52;
      return (
        <Tile>
          {line(`M${x0} ${topY}L${crossL} ${midY}`, ProfitColor)}
          {line(`M${crossL} ${midY}L${K1} ${bottomY}`, LossColor)}
          {line(`M${K1} ${bottomY}H${K2}`, LossColor)}
          {line(`M${K2} ${bottomY}L${crossR} ${midY}`, LossColor)}
          {line(`M${crossR} ${midY}L${xEnd} ${topY}`, ProfitColor)}
        </Tile>
      );
    }

    case "long_call_butterfly":
    case "iron_butterfly": {
      // Tent peaking at K2, flat loss on both sides (same shape in the spec)
      return (
        <Tile>
          {line(`M${x0} ${bottomY}H${K1_3}`, LossColor)}
          {line(`M${K1_3} ${bottomY}L${crossUp} ${midY}`, LossColor)}
          {line(`M${crossUp} ${midY}L${K2_3} ${topY}`, ProfitColor)}
          {line(`M${K2_3} ${topY}L${crossDown} ${midY}`, ProfitColor)}
          {line(`M${crossDown} ${midY}L${K3_3} ${bottomY}`, LossColor)}
          {line(`M${K3_3} ${bottomY}H${xEnd}`, LossColor)}
        </Tile>
      );
    }

    case "iron_condor": {
      // Mesa/trapezoid: loss - up to breakeven - profit plateau - down - loss
      return (
        <Tile>
          {line(`M${x0} ${bottomY}H${K1_4}`, LossColor)}
          {line(`M${K1_4} ${bottomY}L${crossUp4} ${midY}`, LossColor)}
          {line(`M${crossUp4} ${midY}L${K2_4} ${topY}`, ProfitColor)}
          {line(`M${K2_4} ${topY}H${K3_4}`, ProfitColor)}
          {line(`M${K3_4} ${topY}L${crossDown4} ${midY}`, ProfitColor)}
          {line(`M${crossDown4} ${midY}L${K4_4} ${bottomY}`, LossColor)}
          {line(`M${K4_4} ${bottomY}H${xEnd}`, LossColor)}
        </Tile>
      );
    }

    default:
      return (
        <Tile>
          {line(`M${x0} ${bottomY}H${xEnd}`, LossColor)}
        </Tile>
      );
  }
}

