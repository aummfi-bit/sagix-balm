import { CAPTURE_RATE, RATE, type Market, type Selection } from "./markets";

export type Greeks = {
  price: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
};

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

export function putGreeks(
  spot: number,
  strike: number,
  years: number,
  sigma: number,
): Greeks {
  if (years <= 0 || sigma <= 0) {
    return {
      price: Math.max(strike - spot, 0),
      delta: spot < strike ? -1 : 0,
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
  const thetaPerYear =
    -(spot * normPdf(d1) * sigma) / (2 * Math.sqrt(years)) +
    RATE * strike * disc * normCdf(-d2);
  return {
    price: strike * disc * normCdf(-d2) - spot * normCdf(-d1),
    delta: normCdf(d1) - 1,
    gamma: normPdf(d1) / (spot * volT),
    theta: thetaPerYear / 365,
    vega: (spot * normPdf(d1) * Math.sqrt(years)) / 100,
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

export function buildModel(market: Market, sel: Selection, asof: string) {
  const spot = market.spot;
  const anchorDte = Math.max(daysFrom(sel.anchorExpiry, asof), 0);
  const shortDte = Math.max(daysFrom(sel.shortExpiry, asof), 0);
  const anchorIv = ivFor(market, sel.anchorExpiry);
  const shortIv = ivFor(market, sel.shortExpiry);
  const mult = 100 * sel.contracts;

  const anchor = putGreeks(spot, sel.anchorStrike, anchorDte / 365, anchorIv);
  const short = putGreeks(spot, sel.shortStrike, shortDte / 365, shortIv);

  const anchorCost = anchor.price * mult;
  const weeklyIv = ivFor(market, market.tenors[0].expiry);
  const weeklyPremium = putGreeks(
    spot,
    Math.round(spot / market.strikeStep) * market.strikeStep,
    7 / 365,
    weeklyIv,
  ).price;
  const weeklyIncome = weeklyPremium * mult;

  const weeksGross = weeklyIncome > 0 ? anchorCost / weeklyIncome : 0;
  const weeksRealistic = weeksGross / CAPTURE_RATE;

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
    const a = putGreeks(newSpot, sel.anchorStrike, aDte / 365, aIv).price;
    const s = putGreeks(newSpot, sel.shortStrike, sDte / 365, sIv).price;
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

  return {
    market,
    sel,
    asof,
    spot,
    anchor,
    short,
    anchorDte,
    shortDte,
    anchorIv,
    shortIv,
    anchorCost,
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
