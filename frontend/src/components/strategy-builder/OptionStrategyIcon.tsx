import type { ReactNode } from "react";
import type { ReadymadeCardId, TemplateId } from "@/lib/strategy-builder/templates";

type Props = {
  templateId: TemplateId | ReadymadeCardId;
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
    {/* <rect
      className="background"
      width="60"
      height="60"
      transform="translate(1 1)"
      fill="none"
      opacity="0.04"
    /> */}
    {/* <path d="M1.5 1V60.2366" strokeLinecap="square" stroke="currentColor" /> */}
    <path
      d="M1.46154 31H61"
      strokeWidth="0.5"
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

  const strokeCommon = {
    strokeWidth: "1",
    strokeLinecap: "round" as const,
  };

  const line = (d: string, stroke: string) => (
    <path d={d} stroke={stroke} {...strokeCommon} />
  );

  const nakedShortCallTile = (
    <Tile>
      {line(`M${x0} ${topY}H28`, ProfitColor)}
      {line(`M28 ${topY}L36 ${midY}`, ProfitColor)}
      {line(`M36 ${midY}L${K2_2} ${bottomY}`, LossColor)}
    </Tile>
  );

  const nakedShortPutTile = (
    <Tile>
      {line(`M${K1_2} ${K4_4}L26 ${midY}`, LossColor)}
      {line(`M26 ${midY}L34 ${topY}`, ProfitColor)}
      {line(`M34 ${topY}H${xEnd}`, ProfitColor)}
    </Tile>
  );

  const coveredShortCallTile = (
    <Tile>
      {line(`M${x0} ${topY}H28`, ProfitColor)}
      {line(`M28 ${topY}L36 ${midY}`, ProfitColor)}
      {line(`M36 ${midY}L44 ${bottomY}`, LossColor)}
      {line(`M44 ${bottomY}H${xEnd}`, LossColor)}
    </Tile>
  );

  const coveredShortPutTile = (
    <Tile>
      {line(`M${x0} ${bottomY}H18`, LossColor)}
      {line(`M18 ${bottomY}L26 ${midY}`, LossColor)}
      {line(`M26 ${midY}L34 ${topY}`, ProfitColor)}
      {line(`M34 ${topY}H${xEnd}`, ProfitColor)}
    </Tile>
  );



  switch (templateId) {
    case "build-your-own":
      return (
        <svg
          width="60%"
          height="60%"
          viewBox="0 0 20 20"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="mx-auto block text-zinc-600 opacity-80 dark:text-zinc-300"
        >
          <path
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="0.5"
            d="M7.75 4H19M7.75 4a2.25 2.25 0 0 1-4.5 0m4.5 0a2.25 2.25 0 0 0-4.5 0M1 4h2.25m13.5 6H19m-2.25 0a2.25 2.25 0 0 1-4.5 0m4.5 0a2.25 2.25 0 0 0-4.5 0M1 10h11.25m-4.5 6H19M7.75 16a2.25 2.25 0 0 1-4.5 0m4.5 0a2.25 2.25 0 0 0-4.5 0M1 16h2.25"
          />
        </svg>
      );

    case "naked-shorts":
      // Mixed setup: show short call and short put side by side.
      return (
        <div className="grid h-full min-h-0 w-full grid-cols-2 gap-1 sm:gap-0.5">
          {nakedShortCallTile}
          {nakedShortPutTile}
        </div>
      );

    case "covered-shorts":
      // Mixed setup: show covered call and covered put side by side.
      return (
        <div className="grid h-full min-h-0 w-full grid-cols-2 gap-1 sm:gap-0.5">
          {coveredShortCallTile}
          {coveredShortPutTile}
        </div>
      );

    case "bull_call_spread":
    case "bull_put_spread": {
      // Debit call / credit put (bullish): loss flat left -> diagonal -> profit flat right
      return (
        <Tile>
          {line(`M${x0} ${bottomY}H${K1_2}`, LossColor)}
          {line(`M${K1_2} ${bottomY}L${mid_2} ${midY}`, LossColor)}
          {line(`M${mid_2} ${midY}L${K2_2} ${topY}`, ProfitColor)}
          {line(`M${K2_2} ${topY}H${xEnd}`, ProfitColor)}
        </Tile>
      );
    }

    case "bear_put_spread":
    case "bear_call_spread": {
      // Bearish verticals: profit flat left -> diagonal down -> loss flat right
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

    case "short_straddle": {
      // Inverted V: max profit at strike, loss diagonals away from K1
      return (
        <Tile>
          {line(`M${x0} ${bottomY}L${crossLeft} ${midY}`, LossColor)}
          {line(`M${crossLeft} ${midY}L${K_1} ${topY}`, ProfitColor)}
          {line(`M${K_1} ${topY}L${crossRight} ${midY}`, ProfitColor)}
          {line(`M${crossRight} ${midY}L${xEnd} ${bottomY}`, LossColor)}
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

    case "short_strangle": {
      // Inverted bucket: loss wings, flat plateau at max profit between strikes
      const K1 = K1_2;
      const K2 = K2_2;
      const crossL = 10;
      const crossR = 52;
      return (
        <Tile>
          {line(`M${x0} ${bottomY}L${crossL} ${midY}`, LossColor)}
          {line(`M${crossL} ${midY}L${K1} ${topY}`, ProfitColor)}
          {line(`M${K1} ${topY}H${K2}`, ProfitColor)}
          {line(`M${K2} ${topY}L${crossR} ${midY}`, ProfitColor)}
          {line(`M${crossR} ${midY}L${xEnd} ${bottomY}`, LossColor)}
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

