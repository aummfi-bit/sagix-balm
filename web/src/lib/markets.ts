export const ASOF = "2026-08-05";
export const RATE = 0.04;
/** Share of theoretical premium kept after early closes, losing weeks, commissions. */
export const CAPTURE_RATE = 0.75;

export type Tenor = { expiry: string; iv: number };

/**
 * A market in one contract. `null` on a side means nobody is quoting it, which
 * is reported as "no quote" rather than filled in from the other side.
 */
export type QuoteSides = { bid: number | null; ask: number | null };

export type Market = {
  symbol: string;
  label: string;
  spot: number;
  strikeStep: number;
  /** Leverage of 30-day IV to spot. */
  volBeta: number;
  source: string;
  tenors: Tenor[];
  /**
   * Live two-sided markets keyed `${kind}|${expiry}|${strike}`, present only
   * after a `balm sync`. The seeded markets below are close snapshots with no
   * book behind them, so they carry none and every tradable price reads as a
   * dash until a sync fills them in.
   */
  quotes?: Record<string, QuoteSides>;
};

export type Selection = {
  anchorExpiry: string;
  anchorStrike: number;
  shortExpiry: string;
  shortStrike: number;
  contracts: number;
};

export const MARKETS: Market[] = [
  {
    symbol: "GLXY",
    label: "Galaxy Digital",
    spot: 22.7,
    strikeStep: 0.5,
    volBeta: 1.5,
    source: "Barchart / Stocknear close snapshot, Jul 24 2026",
    tenors: [
      { expiry: "2026-08-07", iv: 1.312 },
      { expiry: "2026-08-14", iv: 1.1658 },
      { expiry: "2026-08-21", iv: 1.1309 },
      { expiry: "2026-08-28", iv: 1.0331 },
      { expiry: "2026-09-04", iv: 1.0355 },
      { expiry: "2026-09-18", iv: 1.1006 },
      { expiry: "2026-10-16", iv: 1.0156 },
      { expiry: "2026-12-18", iv: 0.995 },
      { expiry: "2027-01-15", iv: 0.9778 },
      { expiry: "2028-01-21", iv: 0.9 },
    ],
  },
  {
    symbol: "IBIT",
    label: "iShares Bitcoin Trust",
    spot: 36.03,
    strikeStep: 1.0,
    volBeta: 0.7,
    source: "Options Analysis Suite close snapshot, Jul 28 2026",
    tenors: [
      { expiry: "2026-08-07", iv: 0.364 },
      { expiry: "2026-08-14", iv: 0.361 },
      { expiry: "2026-08-21", iv: 0.364 },
      { expiry: "2026-08-28", iv: 0.364 },
      { expiry: "2026-09-04", iv: 0.366 },
      { expiry: "2026-09-18", iv: 0.369 },
      { expiry: "2026-10-16", iv: 0.385 },
      { expiry: "2026-11-20", iv: 0.401 },
      { expiry: "2026-12-18", iv: 0.415 },
      { expiry: "2027-01-15", iv: 0.421 },
      { expiry: "2027-06-17", iv: 0.442 },
      { expiry: "2027-12-17", iv: 0.473 },
      { expiry: "2028-01-21", iv: 0.475 },
    ],
  },
];

/**
 * Mirror a put selection onto the call side: the anchor sits at the money and
 * the weekly one step out, which for calls is a step *above* spot rather than
 * below. Used the first time the call panel is opened for a market.
 */
export function callSelectionFor(
  market: Market,
  put: Selection,
): Selection {
  const atm = Math.round(market.spot / market.strikeStep) * market.strikeStep;
  return {
    ...put,
    anchorStrike: Number(atm.toFixed(2)),
    shortStrike: Number((atm + market.strikeStep).toFixed(2)),
  };
}

export const DEFAULTS: Record<string, Selection> = {
  GLXY: {
    anchorExpiry: "2027-01-15",
    anchorStrike: 22.5,
    shortExpiry: "2026-08-14",
    shortStrike: 22.0,
    contracts: 1,
  },
  IBIT: {
    anchorExpiry: "2027-01-15",
    anchorStrike: 36.0,
    shortExpiry: "2026-08-14",
    shortStrike: 35.0,
    contracts: 1,
  },
};
