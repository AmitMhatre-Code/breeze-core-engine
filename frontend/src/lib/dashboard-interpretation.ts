/**
 * Plain-language labels + tooltip copy for dashboard metrics.
 * Bands are heuristic (not exchange definitions); tune if you recover exact legacy thresholds.
 */

export type InterpretTone = "muted" | "positive" | "caution" | "alarm";

export type MetricInterpretation = {
  label: string;
  /** Shown as the native tooltip (`title` attribute). */
  tooltip: string;
  tone: InterpretTone;
};

/** India VIX (INDVIX) spot — typical retail-facing buckets. */
export function interpretIndiaVix(vix: number): MetricInterpretation {
  if (vix < 12) {
    return {
      label: "Complacent",
      tooltip:
        "VIX is quite low: the market is pricing little near-term fear. Low volatility can persist but sometimes precedes complacency — not a timing signal on its own.",
      tone: "positive",
    };
  }
  if (vix < 15) {
    return {
      label: "Subdued",
      tooltip:
        "Fear gauge is below its long-run average zone for many periods: volatility is muted; moves can still happen, but option premia are usually cheaper.",
      tone: "muted",
    };
  }
  if (vix < 18) {
    return {
      label: "Normal",
      tooltip:
        "VIX is in a middle range: neither unusually calm nor stressed. Use as context for position size and hedges, not as a directional bet.",
      tone: "muted",
    };
  }
  if (vix < 22) {
    return {
      label: "Elevated",
      tooltip:
        "Volatility expectations are elevated: broader daily swings and richer option premia are more typical. Often aligns with news or stress, but can stay ‘high’ for long stretches.",
      tone: "caution",
    };
  }
  if (vix < 28) {
    return {
      label: "High",
      tooltip:
        "Fear gauge is high: the market is pricing meaningful near-term uncertainty. Risks of sharp moves are higher; hedging is relatively more expensive.",
      tone: "alarm",
    };
  }
  return {
    label: "Extreme",
    tooltip:
      "VIX is very high — usually associated with panic or major events. Contrarian traders sometimes look for exhaustion, but risk management comes first.",
    tone: "alarm",
  };
}

/** ATM implied vol as annualized percent (e.g. 16.5 = 16.5% per year). */
export function interpretAtmIvPercent(ivPct: number): MetricInterpretation {
  if (ivPct < 12) {
    return {
      label: "Low",
      tooltip:
        "ATM IV is relatively low for index options: the expected move to this expiry is modest in annualized terms. Premiums are cheaper; break-evens are tighter.",
      tone: "positive",
    };
  }
  if (ivPct < 15) {
    return {
      label: "Subdued",
      tooltip:
        "IV is a bit below a typical ‘busy’ market: volatility is not demanding huge daily ranges, but event risk can still spike IV quickly.",
      tone: "muted",
    };
  }
  if (ivPct < 19) {
    return {
      label: "Normal",
      tooltip:
        "ATM IV is in an ordinary band for NIFTY index options: the 1σ range on the dashboard matches this level of expected fluctuation to expiry.",
      tone: "muted",
    };
  }
  if (ivPct < 24) {
    return {
      label: "Elevated",
      tooltip:
        "IV is elevated: options are pricing wider likely swings to expiry. The 1σ range widens; credit spreads need more cushion; directional trades pay extra for gamma/vega.",
      tone: "caution",
    };
  }
  if (ivPct < 30) {
    return {
      label: "High",
      tooltip:
        "IV is high — event risk, stress, or a sharp prior move may be baked in. Selling premium carries larger tail risk; long-vol structures are comparatively less cheap.",
      tone: "alarm",
    };
  }
  return {
    label: "Extreme",
    tooltip:
      "IV is extremely high (for an ATM read on this expiry): large implied moves; very rich premia. Mean-reversion and trend narratives both get louder — size carefully.",
    tone: "alarm",
  };
}

/**
 * Put:call ratio from **open interest** (puts / calls) on this expiry’s chain.
 * Contrarian labels are conventional sentiment shorthand, not predictions.
 */
export function interpretPcrOi(pcr: number): MetricInterpretation {
  if (pcr >= 1.25) {
    return {
      label: "Contrarian Bullish",
      tooltip:
        "Put OI is heavy vs call OI: participants have built more downside protection or bearish structure. Contrarians sometimes read very high PCR as washed-out / capitulation risk — not a buy signal by itself.",
      tone: "positive",
    };
  }
  if (pcr >= 1.05) {
    return {
      label: "Defensive Tilt",
      tooltip:
        "More put OI than calls: positioning leans defensive or hedge-heavy. Often ‘cautious’ rather than euphoric; pair with price trend and fund flows.",
      tone: "muted",
    };
  }
  if (pcr >= 0.95) {
    return {
      label: "Balanced",
      tooltip:
        "Put and call open interest are in the same ballpark: no extreme skew in OI at this snapshot.",
      tone: "muted",
    };
  }
  if (pcr >= 0.75) {
    return {
      label: "Call-Heavy",
      tooltip:
        "Call OI is larger vs puts: more upside speculation or covered-call style inventory. Can reflect constructive bias or crowded call strikes.",
      tone: "caution",
    };
  }
  return {
      label: "Contrarian Bearish",
    tooltip:
      "Calls dominate OI vs puts: aggressive upside positioning or low demand for puts. Contrarians worry about complacency; trend followers may still prefer strength. Use with price/volume, not alone.",
    tone: "alarm",
  };
}
