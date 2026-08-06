import {
  CAPTURE_RATE,
  RATE,
  type Market,
  type QuoteSides,
  type Selection,
} from "./markets";

export type Greeks = {
  price: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
};

/**
 * Which half of the market a number belongs to. Buying lifts the offer,
 * selling hits the bid; nothing here ever transacts at the mid.
 */
export type Side = "buy" | "sell";

export type OptionKind = "put" | "call";

export function quoteKey(
  kind: OptionKind,
  expiry: string,
  strike: number,
): string {
  return `${kind}|${expiry}|${strike}`;
}

export function quoteFor(
  market: Market,
  kind: OptionKind,
  expiry: string,
  strike: number,
): QuoteSides | null {
  return market.quotes?.[quoteKey(kind, expiry, strike)] ?? null;
}

/** The tradable price on one side, or `null` when that side is empty. */
export function executable(
  quote: QuoteSides | null,
  side: Side,
): number | null {
  const price = side === "buy" ? quote?.ask : quote?.bid;
  return price != null && price > 0 ? price : null;
}

/**
 * Format a price that may not exist.
 *
 * A dash is the whole point: where the book quotes nothing, the desk shows
 * nothing. Substituting a model value here would put an untradable number in
 * a column of tradable ones.
 */
export function usdOrDash(v: number | null | undefined, digits = 2): string {
  return v == null ? "—" : usd(v, digits);
}

export function pctOrDash(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : pct(v, digits);
}

/** Where the underlying has to end up for the sale to have been worth it. */
export function breakevenFor(
  kind: OptionKind,
  strike: number,
  credit: number | null,
): number | null {
  if (credit == null) return null;
  return kind === "put" ? strike - credit : strike + credit;
}

export function isItm(
  kind: OptionKind,
  spot: number,
  strike: number,
): boolean {
  return kind === "put" ? spot < strike : spot > strike;
}

function normCdf(x: number): number {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + p * z);
  const y =
    1 -
    ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1 + sign * y);
}

function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

function intrinsic(
  kind: OptionKind,
  spot: number,
  strike: number,
): number {
  return kind === "put"
    ? Math.max(strike - spot, 0)
    : Math.max(spot - strike, 0);
}

/** Black-Scholes for either right. The two differ only in the last four lines. */
export function optionGreeks(
  kind: OptionKind,
  spot: number,
  strike: number,
  years: number,
  sigma: number,
): Greeks {
  if (years <= 0 || sigma <= 0) {
    const itm = kind === "put" ? spot < strike : spot > strike;
    return {
      price: intrinsic(kind, spot, strike),
      delta: itm ? (kind === "put" ? -1 : 1) : 0,
      gamma: 0,
      theta: 0,
      vega: 0,
    };
  }
  const volT = sigma * Math.sqrt(years);
  const d1 =
    (Math.log(spot / strike) + (RATE + 0.5 * sigma * sigma) * years) / volT;
  const d2 = d1 - volT;
  const disc = Math.exp(-RATE * years);
  const decay = -(spot * normPdf(d1) * sigma) / (2 * Math.sqrt(years));
  const shared = {
    gamma: normPdf(d1) / (spot * volT),
    vega: (spot * normPdf(d1) * Math.sqrt(years)) / 100,
  };

  if (kind === "call") {
    return {
      ...shared,
      price: spot * normCdf(d1) - strike * disc * normCdf(d2),
      delta: normCdf(d1),
      theta: (decay - RATE * strike * disc * normCdf(d2)) / 365,
    };
  }
  return {
    ...shared,
    price: strike * disc * normCdf(-d2) - spot * normCdf(-d1),
    delta: normCdf(d1) - 1,
    theta: (decay + RATE * strike * disc * normCdf(-d2)) / 365,
  };
}


export function termShift(shock30: number, dte: number): number {
  return shock30 * Math.min(Math.sqrt(30 / Math.max(dte, 1)), 4);
}

export function daysFrom(iso: string, asof: string): number {
  return Math.round((Date.parse(iso) - Date.parse(asof)) / 86_400_000);
}

export function fmtDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  return `${d} ${months[Number(m) - 1]} ${y.slice(2)}`;
}

export function usd(v: number, digits = 2): string {
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toFixed(digits)}`;
}

export function pct(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

export function signed(v: number, digits = 2): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;
}

export function ladder(spot: number, step: number, span = 7): number[] {
  const atm = Math.round(spot / step) * step;
  const out: number[] = [];
  for (let i = -span; i <= span; i++) {
    const k = Number((atm + i * step).toFixed(2));
    if (k > 0) out.push(k);
  }
  return out;
}

export function ivFor(market: Market, expiry: string): number {
  const hit = market.tenors.find((t) => t.expiry === expiry);
  return hit ? hit.iv : market.tenors[market.tenors.length - 1].iv;
}

export function buildModel(
  market: Market,
  sel: Selection,
  asof: string,
  kind: OptionKind = "put",
) {
  const spot = market.spot;
  const anchorDte = Math.max(daysFrom(sel.anchorExpiry, asof), 0);
  const shortDte = Math.max(daysFrom(sel.shortExpiry, asof), 0);
  const anchorIv = ivFor(market, sel.anchorExpiry);
  const shortIv = ivFor(market, sel.shortExpiry);
  const mult = 100 * sel.contracts;

  const anchor = optionGreeks(
    kind,
    spot,
    sel.anchorStrike,
    anchorDte / 365,
    anchorIv,
  );
  const short = optionGreeks(kind, spot, sel.shortStrike, shortDte / 365, shortIv);

  // The anchor is bought and the weekly is sold, so they price off opposite
  // sides of the book. No quote on a side means no price on that side: the
  // model's value never stands in for one, and anything derived from a missing
  // price is null rather than estimated.
  const anchorQuote = quoteFor(market, kind, sel.anchorExpiry, sel.anchorStrike);
  const shortQuote = quoteFor(market, kind, sel.shortExpiry, sel.shortStrike);
  const anchorAsk = executable(anchorQuote, "buy");
  const anchorBid = executable(anchorQuote, "sell");
  const shortBid = executable(shortQuote, "sell");
  const shortAsk = executable(shortQuote, "buy");

  const anchorCost = anchorAsk != null ? anchorAsk * mult : null;

  // Benchmark sale: the listed strike nearest the money, in the tenor being
  // rolled. Nearest-listed because that is the contract a bid can exist on,
  // and the selected tenor because that is the one actually being sold — the
  // same convention the snapshot uses, so desk and JSON agree.
  const weeklyExpiry = sel.shortExpiry;
  const weeklyIv = shortIv;
  const weeklyStrike =
    Math.round(spot / market.strikeStep) * market.strikeStep;
  const weeklyTheo = optionGreeks(
    kind,
    spot,
    weeklyStrike,
    shortDte / 365,
    weeklyIv,
  ).price;
  const weeklyPremium = executable(
    quoteFor(market, kind, weeklyExpiry, weeklyStrike),
    "sell",
  );
  const weeklyIncome = weeklyPremium != null ? weeklyPremium * mult : null;

  // The payoff horizon needs a real price at both ends: what the anchor costs
  // to buy and what a week of selling pays.
  const weeksGross =
    anchorCost != null && weeklyIncome != null && weeklyIncome > 0
      ? anchorCost / weeklyIncome
      : null;
  const weeksRealistic = weeksGross != null ? weeksGross / CAPTURE_RATE : null;

  const netDelta = (anchor.delta - short.delta) * mult;
  const netTheta = (anchor.theta - short.theta) * mult;
  const netVega = (anchor.vega - short.vega) * mult;
  const netGamma = (anchor.gamma - short.gamma) * mult;
  const thetaRatio =
    anchor.theta !== 0 ? Math.abs(short.theta / anchor.theta) : 0;

  const ivEdge = shortIv - anchorIv;
  const shape =
    ivEdge > 0.02 ? "backwardation" : ivEdge < -0.02 ? "contango" : "flat";

  function valueAt(
    newSpot: number,
    daysForward: number,
    shock30: number,
  ): number {
    const aDte = Math.max(anchorDte - daysForward, 0);
    const sDte = Math.max(shortDte - daysForward, 0);
    const aIv = Math.max(anchorIv + termShift(shock30, Math.max(aDte, 1)), 0.01);
    const sIv = Math.max(shortIv + termShift(shock30, Math.max(sDte, 1)), 0.01);
    const a = optionGreeks(kind, newSpot, sel.anchorStrike, aDte / 365, aIv).price;
    const s = optionGreeks(kind, newSpot, sel.shortStrike, sDte / 365, sIv).price;
    return (a - s) * mult;
  }

  const base = valueAt(spot, 0, 0);
  const moves = [
    -0.4, -0.3, -0.2, -0.15, -0.1, -0.05, 0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4,
  ];

  function scenarioRow(daysForward: number): number[] {
    return moves.map((m) => {
      const shock =
        m < 0 ? -market.volBeta * m : -market.volBeta * m * 0.5;
      return Number(
        (valueAt(spot * (1 + m), daysForward, shock) - base).toFixed(0),
      );
    });
  }

  // What unwinding the structure would actually pay: sell the anchor into the
  // bid, buy the weekly back at the ask. Null as soon as either side is unquoted.
  const liquidationValue =
    anchorBid != null && shortAsk != null ? (anchorBid - shortAsk) * mult : null;

  // The model's own value of the structure, for comparison against what the
  // market would actually pay to take it off your hands.
  const modelValue = (anchor.price - short.price) * mult;

  return {
    market,
    sel,
    asof,
    kind,
    spot,
    anchor,
    short,
    anchorDte,
    shortDte,
    anchorIv,
    shortIv,
    anchorQuote,
    shortQuote,
    anchorAsk,
    anchorBid,
    shortBid,
    shortAsk,
    anchorCost,
    modelValue,
    liquidationValue,
    weeklyStrike,
    weeklyTheo,
    weeklyPremium,
    weeklyIncome,
    weeksGross,
    weeksRealistic,
    netDelta,
    netTheta,
    netVega,
    netGamma,
    thetaRatio,
    ivEdge,
    shape,
    moves,
    scenarioRow,
  };
}

export type Model = ReturnType<typeof buildModel>;
