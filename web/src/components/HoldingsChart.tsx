"use client";

import { portfolioValueAt, type Holdings } from "@/lib/holdings";
import type { Market } from "@/lib/markets";
import { ladder, num, usd } from "@/lib/pricing";

const RANGE = 0.4;
const TARGET_STRIKE_LABELS = 7;

/**
 * Total book value across a range of stock prices: cash, shares, and every
 * option holding repriced by the model at each hypothetical spot. The
 * dashed lines mark where you actually are right now — today's spot and
 * today's total — so the fill reads directly as gain above, loss below.
 */
export function HoldingsChart({
  market,
  holdings,
  asof,
}: {
  market: Market;
  holdings: Holdings;
  asof: string;
}) {
  const spot = market.spot;
  const minP = spot * (1 - RANGE);
  const maxP = spot * (1 + RANGE);
  // Sampled every half strike step, so a kink at an actual strike — the one
  // place this curve isn't smooth — falls close to a plotted point instead
  // of getting rounded off between two widely spaced ones.
  const sampleStep = market.strikeStep / 2;
  const steps = Math.ceil((maxP - minP) / sampleStep);
  const prices = Array.from(
    { length: steps + 1 },
    (_, i) => minP + ((maxP - minP) * i) / steps,
  );
  const values = prices.map((p) => portfolioValueAt(holdings, market, asof, p));
  const current = portfolioValueAt(holdings, market, asof, spot);

  const width = 720;
  const height = 260;
  const pad = { t: 24, r: 16, b: 36, l: 72 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;

  const minY = Math.min(...values, current);
  const maxY = Math.max(...values, current);
  const span = Math.max(maxY - minY, 1);

  const x = (p: number) => pad.l + ((p - minP) / (maxP - minP)) * plotW;
  const y = (v: number) => pad.t + ((maxY - v) / span) * plotH;

  const baselineY = y(current);
  const spotX = x(spot);

  // Ticked at the strike grid, not arbitrary fractions of the price range —
  // these are the prices that line up with actual calls and puts.
  const strikeDecimals = (String(market.strikeStep).split(".")[1] ?? "").length;
  const strikeSpan = Math.ceil((maxP - minP) / market.strikeStep / 2) + 1;
  const strikeTicks = ladder(spot, market.strikeStep, strikeSpan).filter(
    (k) => k >= minP && k <= maxP,
  );
  const labelEvery = Math.max(
    1,
    Math.round(strikeTicks.length / TARGET_STRIKE_LABELS),
  );
  const strikeLabel = (k: number): string => {
    const s = num(k, strikeDecimals);
    return strikeDecimals > 0 ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
  };

  const linePath = values
    .map((v, i) => `${i === 0 ? "M" : "L"} ${x(prices[i])} ${y(v)}`)
    .join(" ");

  const areaPath =
    `M ${x(prices[0])} ${baselineY} ` +
    values.map((v, i) => `L ${x(prices[i])} ${y(v)}`).join(" ") +
    ` L ${x(prices[prices.length - 1])} ${baselineY} Z`;

  const clipGain = `holdings-chart-gain-${market.symbol}`;
  const clipLoss = `holdings-chart-loss-${market.symbol}`;
  const tickLabel =
    "fill-[var(--text-dim)] text-[10px]";
  const tickFont = { fontFamily: "ui-monospace, monospace" };

  return (
    <section className="space-y-3 rounded-md border border-[var(--border)] bg-[var(--bg-light)] p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--text-strong)]">
          Total book · {market.symbol}
        </h2>
        <span className="text-xs text-[var(--text-dim)]">
          Cash, stock, and every option, marked at each spot
        </span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto"
        role="img"
        aria-label="Total holdings value across stock prices"
      >
        <defs>
          <clipPath id={clipGain}>
            <rect x={0} y={0} width={width} height={Math.max(baselineY, 0)} />
          </clipPath>
          <clipPath id={clipLoss}>
            <rect
              x={0}
              y={baselineY}
              width={width}
              height={Math.max(height - baselineY, 0)}
            />
          </clipPath>
        </defs>
        <path d={areaPath} fill="var(--good)" opacity={0.15} clipPath={`url(#${clipGain})`} />
        <path d={areaPath} fill="var(--bad)" opacity={0.15} clipPath={`url(#${clipLoss})`} />
        <line
          x1={pad.l}
          y1={baselineY}
          x2={width - pad.r}
          y2={baselineY}
          stroke="var(--border)"
          strokeDasharray="4 4"
        />
        <line
          x1={spotX}
          y1={pad.t}
          x2={spotX}
          y2={height - pad.b}
          stroke="var(--border)"
          strokeDasharray="4 4"
        />
        <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth={2} />
        <circle cx={spotX} cy={y(current)} r={4} fill="var(--accent)" />
        <text x={spotX} y={pad.t - 8} textAnchor="middle" className={tickLabel} style={tickFont}>
          {usd(spot, 2)} now
        </text>
        <text
          x={width - pad.r}
          y={baselineY - 6}
          textAnchor="end"
          className={tickLabel}
          style={tickFont}
        >
          {usd(current, 0)} today
        </text>
        {[maxY, current, minY].map((v, i) => (
          <text
            key={i}
            x={pad.l - 8}
            y={y(v) + 3}
            textAnchor="end"
            className={tickLabel}
            style={tickFont}
          >
            {usd(v, 0)}
          </text>
        ))}
        {strikeTicks.map((k, i) =>
          i % labelEvery === 0 ? (
            <text
              key={k}
              x={x(k)}
              y={height - 10}
              textAnchor="middle"
              className={tickLabel}
              style={tickFont}
            >
              {strikeLabel(k)}
            </text>
          ) : null,
        )}
      </svg>
      <p className="text-xs text-[var(--text-dim)]">
        Model prices at each spot, with today&apos;s implied vol and days to
        expiry held fixed — spot sensitivity alone, not a forecast forward in
        time. Green is above today&apos;s {usd(current, 0)}, red is below.
      </p>
    </section>
  );
}
