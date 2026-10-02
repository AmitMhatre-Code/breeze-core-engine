import {
  formatPositionDelta,
  netDeltaTitle,
  positionDeltaTitle,
  positionDeltaToneClass,
  type PositionDelta,
} from "@/lib/strategy-builder/greeks";
import type { PortfolioGreeks } from "@/lib/strategy-builder/payoff";

/** One leg's position delta (side × quantity × option delta), with the breakdown on hover. */
export function PositionDeltaText({
  delta,
  lotSize,
  className = "",
}: {
  delta: PositionDelta | null | undefined;
  lotSize?: number | null;
  className?: string;
}) {
  if (delta === undefined) return <span className={`text-muted ${className}`}>—</span>;
  return (
    <span
      className={`${positionDeltaToneClass(delta?.position)} ${className}`}
      title={positionDeltaTitle(delta, lotSize)}
    >
      {formatPositionDelta(delta?.position)}
    </span>
  );
}

/** A strategy's or group's summed delta, in units of the underlying. */
export function NetDeltaText({
  net,
  lotSize,
  className = "",
}: {
  net: number | null;
  lotSize?: number | null;
  className?: string;
}) {
  return (
    <span
      className={`${positionDeltaToneClass(net)} ${className}`}
      title={netDeltaTitle(net, lotSize)}
    >
      {formatPositionDelta(net)}
    </span>
  );
}

/**
 * The payoff panels' Greeks line. Follows the panel's "what if" controls (IV shock, DTE),
 * so with those at their defaults its delta equals the legs table's Net Δ.
 */
export function ScenarioGreeksLine({
  greeks,
  lotSize,
  className = "",
}: {
  greeks: PortfolioGreeks | null;
  lotSize?: number | null;
  className?: string;
}) {
  const num = (v: number | undefined, digits: number) =>
    v != null && Number.isFinite(v) ? v.toFixed(digits) : "—";
  return (
    <div className={`flex flex-wrap gap-x-6 gap-y-2 text-xs ${className}`}>
      <span className="text-muted">
        Delta{" "}
        <NetDeltaText
          net={greeks?.delta ?? null}
          lotSize={lotSize}
          className="font-mono font-semibold tabular-nums"
        />
      </span>
      <span className="text-muted">
        Gamma{" "}
        <span className="font-mono font-semibold tabular-nums text-foreground">
          {num(greeks?.gamma, 6)}
        </span>
      </span>
      <span className="text-muted">
        Vega{" "}
        <span className="font-mono font-semibold tabular-nums text-foreground">
          {num(greeks?.vega, 4)}
        </span>
      </span>
      <span className="text-muted">
        Theta / day{" "}
        <span className="font-mono font-semibold tabular-nums text-foreground">
          {num(greeks?.thetaPerDay, 4)}
        </span>
      </span>
    </div>
  );
}

/** Body of the Δ column's help popover. */
export function DeltaHelp() {
  return (
    <>
      Position delta: how many units of the underlying this leg behaves like right now —
      option delta × quantity, negative when sold. Legs add up exactly to Net Δ. Priced off
      the forward implied by the chain (put-call parity), each strike&apos;s own IV and the
      time left to the 15:30 close on expiry day. Hover a value for its breakdown.
    </>
  );
}
