"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
import {
  ASOF,
  CAPTURE_RATE,
  DEFAULTS,
  MARKETS,
  RATE,
  callSelectionFor,
  type Selection,
} from "@/lib/markets";
import {
  breakevenFor,
  buildModel,
  daysFrom,
  executable,
  fmtDate,
  isItm,
  ivFor,
  ladder,
  optionGreeks,
  pct,
  pctOrDash,
  quoteFor,
  signed,
  usd,
  usdOrDash,
  type Model,
  type OptionKind,
} from "@/lib/pricing";
import {
  getHoldings,
  getServerHoldings,
  holdingsFor,
  setHoldings,
  subscribeHoldings,
  type Holdings,
} from "@/lib/holdings";
import type { DeskData } from "@/lib/snapshot";
import { HoldingsPanel } from "./HoldingsPanel";
import { QuoteStatus } from "./QuoteStatus";
import { ScenarioChart } from "./ScenarioChart";

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex flex-col gap-1.5 min-w-0">
      <span className="text-[11px] uppercase tracking-[0.14em] text-[var(--text-dim)]">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-light)] px-3 py-2 text-sm text-[var(--text-strong)] outline-none focus:border-[var(--accent)]"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Stat({
  value,
  label,
  tone,
}: {
  value: string;
  label: string;
  tone?: "good" | "warn" | "bad" | "neutral";
}) {
  const color =
    tone === "good"
      ? "text-[var(--ok)]"
      : tone === "warn"
        ? "text-[var(--warn)]"
        : tone === "bad"
          ? "text-[var(--bad)]"
          : "text-[var(--text-strong)]";
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--bg-light)] px-4 py-3">
      <div className={`font-mono text-xl tabular-nums tracking-tight ${color}`}>
        {value}
      </div>
      <div className="mt-1 text-[11px] uppercase tracking-[0.12em] text-[var(--text-dim)]">
        {label}
      </div>
    </div>
  );
}

function Controls({
  model,
  onChange,
}: {
  model: Model;
  onChange: (next: Selection) => void;
}) {
  const { market, sel, asof } = model;
  const strikes = ladder(market.spot, market.strikeStep);
  const longTenors = market.tenors.filter((t) => daysFrom(t.expiry, asof) > 60);
  const shortTenors = market.tenors.filter(
    (t) => daysFrom(t.expiry, asof) <= 60 && daysFrom(t.expiry, asof) > 0,
  );

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <SelectField
        label="Anchor expiry"
        value={sel.anchorExpiry}
        onChange={(v) => onChange({ ...sel, anchorExpiry: v })}
        options={longTenors.map((t) => ({
          value: t.expiry,
          label: `${fmtDate(t.expiry)} · ${daysFrom(t.expiry, asof)}d · ${pct(t.iv, 0)} IV`,
        }))}
      />
      <SelectField
        label="Anchor strike"
        value={String(sel.anchorStrike)}
        onChange={(v) => onChange({ ...sel, anchorStrike: Number(v) })}
        options={strikes.map((k) => ({
          value: String(k),
          label: k.toFixed(2),
        }))}
      />
      <SelectField
        label="Short expiry"
        value={sel.shortExpiry}
        onChange={(v) => onChange({ ...sel, shortExpiry: v })}
        options={shortTenors.map((t) => ({
          value: t.expiry,
          label: `${fmtDate(t.expiry)} · ${daysFrom(t.expiry, asof)}d · ${pct(t.iv, 0)} IV`,
        }))}
      />
      <SelectField
        label="Short strike"
        value={String(sel.shortStrike)}
        onChange={(v) => onChange({ ...sel, shortStrike: Number(v) })}
        options={strikes.map((k) => ({
          value: String(k),
          label: k.toFixed(2),
        }))}
      />
    </div>
  );
}

function DataTable({
  headers,
  rows,
  alignRight,
  rowTone,
}: {
  headers: string[];
  rows: string[][];
  alignRight?: boolean[];
  rowTone?: Array<"good" | "warn" | "bad" | "info" | undefined>;
}) {
  const toneDot = {
    good: "bg-[var(--ok)]",
    warn: "bg-[var(--warn)]",
    bad: "bg-[var(--bad)]",
    info: "bg-[var(--accent)]",
  };

  return (
    <div className="overflow-x-auto rounded-md border border-[var(--border)]">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] bg-[var(--bg-light)]">
            {headers.map((h, i) => (
              <th
                key={h}
                className={`px-3 py-2 text-[11px] font-medium uppercase tracking-[0.1em] text-[var(--text-dim)] ${
                  alignRight?.[i] ? "text-right" : "text-left"
                }`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr
              key={ri}
              className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--table-hover)]"
            >
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className={`px-3 py-2 font-mono tabular-nums text-[var(--text)] ${
                    alignRight?.[ci] ? "text-right" : "text-left"
                  }`}
                >
                  {ci === 0 && rowTone?.[ri] ? (
                    <span className="inline-flex items-center gap-2">
                      <span
                        className={`inline-block h-1.5 w-1.5 rounded-full ${toneDot[rowTone[ri]!]}`}
                      />
                      {cell}
                    </span>
                  ) : (
                    cell
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LegsTable({ model }: { model: Model }) {
  const { sel, anchor, short, kind } = model;
  const mult = 100 * sel.contracts;
  const suffix = kind === "put" ? "P" : "C";
  // You buy the anchor and sell the weekly, so "to trade" is the ask on one
  // and the bid on the other. Neither is the mid, and neither is the model.
  const netToTrade =
    model.anchorAsk != null && model.shortBid != null
      ? model.anchorAsk - model.shortBid
      : null;

  return (
    <div className="space-y-3">
      <DataTable
        headers={[
          "Leg",
          "Strike",
          "Expiry",
          "DTE",
          "IV",
          "Model",
          "Bid",
          "Ask",
          "To trade",
          "Delta",
          "Theta $/day",
          "Vega $/pt",
        ]}
        alignRight={[
          false, true, false, true, true, true, true, true, true, true, true,
          true,
        ]}
        rowTone={["info", "warn", undefined]}
        rows={[
          [
            `Anchor (long ${kind})`,
            `${sel.anchorStrike.toFixed(2)}${suffix}`,
            fmtDate(sel.anchorExpiry),
            `${model.anchorDte}d`,
            pct(model.anchorIv, 0),
            usd(anchor.price),
            usdOrDash(model.anchorBid),
            usdOrDash(model.anchorAsk),
            usdOrDash(model.anchorAsk),
            signed(anchor.delta, 3),
            signed(anchor.theta * mult, 2),
            signed(anchor.vega * mult, 2),
          ],
          [
            `Weekly (short ${kind})`,
            `${sel.shortStrike.toFixed(2)}${suffix}`,
            fmtDate(sel.shortExpiry),
            `${model.shortDte}d`,
            pct(model.shortIv, 0),
            usd(short.price),
            usdOrDash(model.shortBid),
            usdOrDash(model.shortAsk),
            usdOrDash(model.shortBid),
            signed(-short.delta, 3),
            signed(-short.theta * mult, 2),
            signed(-short.vega * mult, 2),
          ],
          [
            "Net debit",
            "",
            "",
            "",
            "",
            usd(anchor.price - short.price),
            "",
            "",
            usdOrDash(netToTrade),
            signed(model.netDelta / mult, 3),
            signed(model.netTheta, 2),
            signed(model.netVega, 2),
          ],
        ]}
      />
      <p className="text-sm text-[var(--text-dim)]">
        <strong className="text-[var(--text-strong)]">To trade</strong> is the
        side you actually cross: the ask to buy the anchor, the bid to sell the
        weekly. A dash means that side is not quoted in this snapshot — run{" "}
        <code className="text-[var(--text)]">balm sync</code> against TWS for a
        live book.
      </p>
    </div>
  );
}

function StrikeLadder({ model }: { model: Model }) {
  const { market, sel, kind } = model;
  const years = model.shortDte / 365;
  // Sellable strikes run away from the money: below spot for puts, above for
  // calls, with a little the other side for context.
  const strikes = ladder(market.spot, market.strikeStep, 6).filter((k) =>
    kind === "put" ? k <= market.spot * 1.06 : k >= market.spot * 0.94,
  );
  const rows = strikes
    .slice()
    .reverse()
    .map((k) => {
      const g = optionGreeks(kind, market.spot, k, years, model.shortIv);
      const quote = quoteFor(market, kind, sel.shortExpiry, k);
      // Selling this strike pays the bid. Where nothing is bid there is no
      // credit, so there is no breakeven and no payoff pace either.
      const credit = executable(quote, "sell");
      const income = credit != null ? credit * 100 * sel.contracts : null;
      const weeks =
        model.anchorCost != null && income != null && income > 0
          ? model.anchorCost / income / CAPTURE_RATE
          : null;
      const breakeven = breakevenFor(kind, k, credit);
      return {
        k,
        cells: [
          k.toFixed(2),
          usd(g.price),
          usdOrDash(credit),
          usdOrDash(executable(quote, "buy")),
          signed(g.delta, 3),
          g.gamma.toFixed(4),
          signed(g.theta * 100 * sel.contracts, 2),
          usdOrDash(breakeven),
          pctOrDash(
            breakeven != null ? breakeven / market.spot - 1 : null,
            1,
          ),
          weeks != null ? `${weeks.toFixed(1)} wk` : "—",
        ],
      };
    });

  const tones = rows.map((r) => {
    const d = Math.abs(
      optionGreeks(kind, market.spot, r.k, years, model.shortIv).delta,
    );
    if (r.k === sel.shortStrike) return "info" as const;
    if (d > 0.55) return "bad" as const;
    if (d >= 0.3 && d <= 0.5) return "good" as const;
    return undefined;
  });

  return (
    <div className="space-y-3">
      <DataTable
        headers={[
          "Strike",
          "Model",
          "Bid",
          "Ask",
          "Delta",
          "Gamma",
          "Theta $/day",
          "Breakeven",
          "vs spot",
          "Payoff pace",
        ]}
        alignRight={[
          true, true, true, true, true, true, true, true, true, true,
        ]}
        rows={rows.map((r) => r.cells)}
        rowTone={tones}
      />
      <p className="text-sm text-[var(--text-dim)]">
        Selling a strike pays the <strong className="text-[var(--text-strong)]">bid</strong>,
        so the breakeven and payoff pace are struck from it. Where nothing is
        bid there is no credit to compute them from and the cells stay empty.
      </p>
      <p className="text-sm text-[var(--text-dim)]">
        {fmtDate(sel.shortExpiry)} {kind}s at {pct(model.shortIv, 0)} IV. Green
        rows sit in the 0.30–0.50 delta band; red is past 0.55; blue is your
        current short.
      </p>
    </div>
  );
}

function RollChecklist({ model }: { model: Model }) {
  const absDelta = Math.abs(model.short.delta);
  const itm = isItm(model.kind, model.spot, model.sel.shortStrike);
  const modelValue = model.modelValue;
  const weekday = new Date(`${model.asof}T12:00:00Z`).getUTCDay();
  const pastCutoff = weekday > 3 || weekday === 0;
  const rollDirection = model.kind === "put" ? "down" : "up";

  const checks = [
    {
      ok: model.shortDte > 2,
      label: "Expiration gamma",
      detail:
        model.shortDte > 2
          ? `${model.shortDte} DTE. Gamma ${model.short.gamma.toFixed(4)} still manageable.`
          : `${model.shortDte} DTE. Gamma peaking — close or roll now.`,
    },
    {
      ok: !pastCutoff || model.shortDte > 4,
      label: "Roll window",
      detail: pastCutoff
        ? "Past Wednesday cutoff. Do not carry into Thursday/Friday."
        : "Inside the Monday–Wednesday roll window.",
    },
    {
      ok: absDelta <= 0.55,
      label: "Strike drift",
      detail:
        absDelta <= 0.55
          ? `Short delta ${absDelta.toFixed(2)}, still near the money.`
          : `Short delta ${absDelta.toFixed(2)} past 0.55. Roll ${rollDirection}.`,
    },
    {
      ok: !itm,
      label: "Assignment risk",
      detail: itm
        ? `Spot ${usd(model.spot)} ${model.kind === "put" ? "below" : "above"} the ${usd(model.sel.shortStrike)} strike — in the money.`
        : `Spot ${usd(model.spot)} ${model.kind === "put" ? "above" : "below"} ${usd(model.sel.shortStrike)} — OTM.`,
    },
    {
      ok: model.netTheta > 0,
      label: "Theta engine",
      detail: `Weekly decays ${model.thetaRatio.toFixed(1)}× faster than the anchor, netting ${usd(model.netTheta)}/day.`,
    },
    {
      ok: Math.abs(model.netDelta) < 15,
      label: "Directional exposure",
      detail:
        model.netDelta > 0
          ? `Net ${signed(model.netDelta, 1)} delta — long the stock; loses on the first leg down.`
          : `Net ${signed(model.netDelta, 1)} delta — short the stock with downside participation.`,
    },
    // Closing the weekly means paying the ask, so that is the number the exit
    // has to be judged against — and its absence is worth saying out loud.
    {
      ok: model.shortAsk != null,
      label: "Cost to close",
      detail:
        model.shortAsk != null
          ? `Buying the weekly back costs ${usd(model.shortAsk)} at the ask, ${usd(model.shortAsk * 100 * model.sel.contracts)} for the position.`
          : "No ask quoted for the weekly, so the exit cannot be priced. Model values are not fills.",
    },
    {
      ok: model.liquidationValue != null,
      label: "Unwind value",
      detail:
        model.liquidationValue != null
          ? `Selling the anchor at ${usd(model.anchorBid!)} and covering the weekly at ${usd(model.shortAsk!)} nets ${usd(model.liquidationValue)}, ${usd(Math.abs(model.liquidationValue - modelValue))} ${model.liquidationValue < modelValue ? "below" : "above"} the model's ${usd(modelValue)}.`
          : "Both legs need a quote on their exit side before the structure can be valued at what it would fetch.",
    },
  ];

  return (
    <DataTable
      headers={["Check", "Status", "Detail"]}
      rows={checks.map((c) => [c.label, c.ok ? "Clear" : "Act", c.detail])}
      rowTone={checks.map((c) => (c.ok ? "good" : "bad"))}
    />
  );
}
/**
 * One calendar, on one right. The puts panel and the calls panel are the same
 * analysis pointed at a different half of the chain: a long-dated anchor
 * financed by weekly sales against it.
 */
function CalendarPanel({
  model,
  onChange,
}: {
  model: Model;
  onChange: (next: Selection) => void;
}) {
  const { market, sel, kind } = model;
  const noun = kind === "put" ? "put" : "call";

  const termTone =
    model.shape === "backwardation"
      ? "border-[var(--ok)]/40 bg-[var(--ok)]/10"
      : model.shape === "contango"
        ? "border-[var(--warn)]/40 bg-[var(--warn)]/10"
        : "border-[var(--border)] bg-[var(--bg-light)]";

  const termBody =
    model.shape === "backwardation"
      ? `You sell ${pct(model.shortIv, 0)} vol on the weekly and buy ${pct(model.anchorIv, 0)} on the anchor. The surface pays you ${pct(model.ivEdge, 1)} to run this calendar.`
      : model.shape === "contango"
        ? `You sell ${pct(model.shortIv, 0)} vol on the weekly but pay ${pct(model.anchorIv, 0)} for the anchor — a ${pct(Math.abs(model.ivEdge), 1)} structural headwind.`
        : `Front and long-dated vol are within ${pct(Math.abs(model.ivEdge), 1)}. No structural edge either way.`;

  // The payoff bar needs a real cost and a real credit. Without both there is
  // nothing to draw, and drawing it from model values would be a fiction.
  const weeks =
    model.weeksRealistic != null
      ? Math.min(Math.ceil(model.weeksRealistic), 20)
      : null;
  const perWeek =
    model.weeklyIncome != null ? model.weeklyIncome * CAPTURE_RATE : null;

  return (
    <div className="space-y-10">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat value={usd(model.spot)} label={`${market.symbol} spot`} />
        <Stat
          value={usdOrDash(model.anchorCost, 0)}
          label="Anchor debit (ask)"
        />
        <Stat
          value={signed(model.netDelta, 1)}
          label="Net delta (shares)"
          tone={
            Math.abs(model.netDelta) < 8
              ? "good"
              : model.netDelta > 0
                ? "warn"
                : "neutral"
          }
        />
        <Stat
          value={`${usd(model.netTheta, 2)}/day`}
          label="Net theta"
          tone={model.netTheta > 0 ? "good" : "bad"}
        />
        <Stat
          value={
            model.weeksRealistic != null
              ? `${model.weeksRealistic.toFixed(1)} wk`
              : "—"
          }
          label="Weeks to pay off"
        />
      </div>

      <div className={`rounded-md border px-4 py-3 ${termTone}`}>
        <div className="text-sm font-medium text-[var(--text-strong)]">
          Volatility term structure: {model.shape}
        </div>
        <p className="mt-1 text-sm text-[var(--text-dim)]">{termBody}</p>
      </div>

      <section className="space-y-4">
        <h2 className="text-2xl font-semibold text-[var(--text-strong)]">
          Structure
        </h2>
        <Controls model={model} onChange={onChange} />
        <LegsTable model={model} />
      </section>

      <section className="space-y-4">
        <h2 className="text-2xl font-semibold text-[var(--text-strong)]">
          Paying off the anchor
        </h2>
        {weeks != null && perWeek != null && model.anchorCost != null ? (
          <div className="space-y-2">
            <div className="flex justify-between text-xs text-[var(--text-dim)]">
              <span>
                {model.weeksRealistic!.toFixed(1)} sales to cover the anchor
              </span>
              <span>
                {usd(perWeek, 0)} net per sale vs {usd(model.anchorCost, 0)}{" "}
                debit
              </span>
            </div>
            <div className="flex h-3 overflow-hidden rounded-full bg-[var(--border)]">
              {Array.from({ length: weeks }, (_, i) => (
                <div
                  key={i}
                  className="h-full border-r border-[var(--bg)]/40 last:border-0"
                  style={{
                    width: `${100 / weeks}%`,
                    background:
                      i % 2 === 0 ? "var(--accent)" : "var(--accent-2)",
                  }}
                />
              ))}
            </div>
            <p className="text-sm text-[var(--text-dim)]">
              Each segment is one sale of the {model.weeklyStrike.toFixed(2)}{" "}
              {noun} expiring {fmtDate(sel.shortExpiry)} ({model.shortDte}d) at{" "}
              {usd(model.weeklyPremium!)} per share — the bid, which is what a
              seller is paid — kept at {pct(CAPTURE_RATE, 0)} after early closes
              and commissions. Gross count is {model.weeksGross!.toFixed(1)}{" "}
              sales at full capture against the anchor&apos;s ask.
            </p>
          </div>
        ) : (
          <p className="rounded-md border border-[var(--border)] bg-[var(--bg-light)] px-4 py-3 text-sm text-[var(--text-dim)]">
            The payoff horizon needs a price at both ends — the ask that buys
            the anchor and the bid a weekly sale pays. This snapshot quotes{" "}
            {model.anchorCost == null && model.weeklyPremium == null
              ? "neither"
              : model.anchorCost == null
                ? "no anchor ask"
                : "no weekly bid"}
            , so there is nothing to count. Run{" "}
            <code className="text-[var(--text)]">balm sync</code> against TWS
            for a live book.
          </p>
        )}
      </section>

      <section className="space-y-4">
        <h2 className="text-2xl font-semibold text-[var(--text-strong)]">
          Profit and loss across spot moves
        </h2>
        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-light)] p-4">
          <ScenarioChart
            categories={model.moves.map(
              (m) => `${m >= 0 ? "+" : ""}${(m * 100).toFixed(0)}%`,
            )}
            series={[
              {
                name: "1 day",
                data: model.scenarioRow(1),
                color: "var(--text-dim)",
              },
              {
                name: "3 days",
                data: model.scenarioRow(3),
                color: "var(--accent)",
              },
              {
                name: "Weekly expiry",
                data: model.scenarioRow(model.shortDte),
                color: "var(--warn)",
              },
            ]}
          />
          <div className="mt-3 flex flex-wrap gap-4 text-xs text-[var(--text-dim)]">
            <span>
              <i
                className="mr-1.5 inline-block h-2 w-2 rounded-full"
                style={{ background: "var(--text-dim)" }}
              />
              1 day
            </span>
            <span>
              <i
                className="mr-1.5 inline-block h-2 w-2 rounded-full"
                style={{ background: "var(--accent)" }}
              />
              3 days
            </span>
            <span>
              <i
                className="mr-1.5 inline-block h-2 w-2 rounded-full"
                style={{ background: "var(--warn)" }}
              />
              At weekly expiry
            </span>
          </div>
        </div>
        <p className="text-sm text-[var(--text-dim)]">
          Model P&amp;L, at model prices: vol shocked with spot at beta{" "}
          {market.volBeta}, damped by √T across the term structure — the short
          leg reacts far more than the anchor.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-2xl font-semibold text-[var(--text-strong)]">
          This week&apos;s strike ladder
        </h2>
        <StrikeLadder model={model} />
      </section>

      <section className="space-y-4">
        <h2 className="text-2xl font-semibold text-[var(--text-strong)]">
          Roll checklist
        </h2>
        <RollChecklist model={model} />
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-light)] p-5">
          <h3 className="mb-3 text-lg font-semibold text-[var(--text-strong)]">
            Weekly operating loop
          </h3>
          <ul className="space-y-2 text-sm text-[var(--text-dim)]">
            <li>
              <strong className="text-[var(--text-strong)]">Monday.</strong>{" "}
              Mark the anchor and open short. Update banked premium.
            </li>
            <li>
              <strong className="text-[var(--text-strong)]">Tue / Wed.</strong>{" "}
              Close once ~80% of credit is captured. Never carry into Thursday.
            </li>
            <li>
              <strong className="text-[var(--text-strong)]">
                Same session.
              </strong>{" "}
              Sell next weekly by target delta, not fixed distance from spot.
            </li>
            <li>
              <strong className="text-[var(--text-strong)]">Always.</strong>{" "}
              Price the exit off the ask and the entry off the bid — the mid is
              a display value, not a fill, and the spread is a tax on the payoff
              horizon.
            </li>
          </ul>
        </div>
        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-light)] p-5">
          <h3 className="mb-3 text-lg font-semibold text-[var(--text-strong)]">
            What actually goes wrong
          </h3>
          {kind === "put" ? (
            <ul className="space-y-2 text-sm text-[var(--text-dim)]">
              <li>
                <strong className="text-[var(--text-strong)]">
                  Sharp rally.
                </strong>{" "}
                Worst case: anchor loses on delta and vega while credits shrink.
              </li>
              <li>
                <strong className="text-[var(--text-strong)]">
                  Fast crash.
                </strong>{" "}
                Short IV spikes more than the anchor; assignment risk rises.
              </li>
              <li>
                <strong className="text-[var(--text-strong)]">
                  Grind lower.
                </strong>{" "}
                Best case — roll strikes down, collect, anchor gains.
              </li>
              <li>
                <strong className="text-[var(--text-strong)]">
                  Assignment.
                </strong>{" "}
                Becomes a married put — legitimate, but capital-heavy.
              </li>
            </ul>
          ) : (
            <ul className="space-y-2 text-sm text-[var(--text-dim)]">
              <li>
                <strong className="text-[var(--text-strong)]">
                  Sharp crash.
                </strong>{" "}
                Worst case: the anchor call loses on delta and vega while every
                subsequent credit shrinks.
              </li>
              <li>
                <strong className="text-[var(--text-strong)]">
                  Fast rally.
                </strong>{" "}
                The short call goes in the money and caps the upside the anchor
                was bought for.
              </li>
              <li>
                <strong className="text-[var(--text-strong)]">
                  Grind higher.
                </strong>{" "}
                Best case — roll strikes up, collect, anchor gains.
              </li>
              <li>
                <strong className="text-[var(--text-strong)]">
                  Assignment.
                </strong>{" "}
                Short stock against a long call unless the anchor is exercised —
                check the borrow before letting it happen.
              </li>
            </ul>
          )}
        </div>
      </section>

      <p className="text-xs text-[var(--text-dim)]">
        Front IV for the selected short is {pct(ivFor(market, sel.shortExpiry), 0)}.
      </p>
    </div>
  );
}

const PANELS = [
  { kind: "put" as const, label: "Puts", blurb: "Long-dated put anchor, weekly short puts" },
  { kind: "call" as const, label: "Calls", blurb: "Long-dated call anchor, weekly short calls" },
];

/**
 * The desk renders whatever snapshot was committed — there is no refresh
 * button, because refreshing is `balm quotes` (or `balm sync`) writing a new
 * snapshot, not something a page view can do. The status line says whether
 * the quote feed is up and how old these prices are.
 */
export function CalendarDesk({ initial }: { initial?: DeskData }) {
  const markets = initial?.markets ?? MARKETS;
  const asof = initial?.asof ?? ASOF;

  const [active, setActive] = useState(markets[0]?.symbol ?? "GLXY");
  const [panel, setPanel] = useState<OptionKind | null>("put");
  const [selections, setSelections] = useState<Record<string, Selection>>({
    ...DEFAULTS,
    ...(initial?.selections ?? {}),
  });

  // Manual holdings live in localStorage, which only the browser can read.
  const holdings = useSyncExternalStore(
    subscribeHoldings,
    getHoldings,
    getServerHoldings,
  );

  const market = markets.find((m) => m.symbol === active) ?? markets[0];

  // Selections are per symbol *and* per right: the call side wants its own
  // strikes, mirrored above the money rather than below it.
  const selKey = (kind: OptionKind) =>
    kind === "put" ? market.symbol : `${market.symbol}|call`;
  const putSel = selections[market.symbol] ?? DEFAULTS[market.symbol];
  const sel =
    panel == null
      ? putSel
      : (selections[selKey(panel)] ??
        (panel === "call" ? callSelectionFor(market, putSel) : putSel));

  const model = useMemo(
    () => buildModel(market, sel, asof, panel ?? "put"),
    [market, sel, asof, panel],
  );

  const update = (next: Selection) =>
    setSelections((prev) => ({ ...prev, [selKey(panel ?? "put")]: next }));

  const updateHoldings = (next: Holdings) =>
    setHoldings({ ...holdings, [market.symbol]: next });

  return (
    <div className="mx-auto w-full max-w-6xl space-y-10 px-4 pb-10 pt-0 sm:px-6 lg:px-8">
      <QuoteStatus
        symbol={market.symbol}
        quoteSource={initial?.quoteSource ?? null}
        // Per-market, since the feed stamps each symbol separately.
        quotesAsOf={market.quotesAsOf ?? initial?.quotesAsOf ?? null}
        delayMinutes={initial?.quoteDelayMinutes ?? null}
      />

      <header className="space-y-3 pt-6">
        <p className="text-sm font-medium tracking-[0.28em] uppercase text-[var(--accent)]">
          sagix balm
        </p>
        <h1 className="text-4xl font-semibold leading-tight tracking-tight text-[var(--text-strong)] sm:text-5xl">
          Calendar desk
        </h1>
        <p className="max-w-2xl text-[var(--text-dim)]">
          A long-dated anchor financed by weekly short options. Modeled as of{" "}
          {fmtDate(asof)} at a {pct(RATE, 0)} risk-free rate. Adjust strikes and
          expiries — greeks and scenarios recompute live.
        </p>
      </header>

      {/* Ticker, then what you hold in it, then the desks. */}
      <div className="flex flex-wrap items-center gap-2">
        {markets.map((m) => (
          <button
            key={m.symbol}
            type="button"
            onClick={() => setActive(m.symbol)}
            className={`rounded-md px-3.5 py-2 font-mono text-sm transition ${
              m.symbol === active
                ? "bg-[var(--accent)] font-semibold text-[#0d1117]"
                : "border border-[var(--border)] text-[var(--text-dim)] hover:bg-[var(--accent-faint)] hover:text-[var(--text-strong)]"
            }`}
          >
            {m.symbol}
          </button>
        ))}
        <span className="ml-auto text-xs text-[var(--text-dim)]">
          {market.label} · {market.source}
        </span>
      </div>

      <HoldingsPanel
        market={market}
        holdings={holdingsFor(holdings, market.symbol)}
        onChange={updateHoldings}
      />

      <div className="flex flex-wrap items-center gap-2">
        {PANELS.map((p) => {
          const open = panel === p.kind;
          return (
            <button
              key={p.kind}
              type="button"
              aria-expanded={open}
              onClick={() => setPanel(open ? null : p.kind)}
              className={`rounded-md px-4 py-2 text-sm transition ${
                open
                  ? "bg-[var(--accent)] font-semibold text-[#0d1117]"
                  : "border border-[var(--border)] text-[var(--text-dim)] hover:bg-[var(--accent-faint)] hover:text-[var(--text-strong)]"
              }`}
            >
              {open ? "▾" : "▸"} {p.label}
            </button>
          );
        })}
        <span className="ml-auto text-xs text-[var(--text-dim)]">
          {panel
            ? PANELS.find((p) => p.kind === panel)?.blurb
            : "Both desks closed"}
        </span>
      </div>

      {panel ? (
        <section className="rounded-md border border-[var(--border)] p-5 sm:p-6">
          <h2 className="mb-6 text-xl font-semibold text-[var(--text-strong)]">
            {panel === "put" ? "Put calendar" : "Call calendar"} · {market.symbol}
          </h2>
          <CalendarPanel model={model} onChange={update} />
        </section>
      ) : null}

      <footer className="border-t border-[var(--border)] pt-6 text-xs text-[var(--text-dim)]">
        <p>
          Prices take the side of the trade: you buy the anchor at the ask and
          sell the weekly at the bid.{" "}
          {market.quotes
            ? "Quotes come from the last balm sync; a dash means that side is not quoted."
            : "This snapshot carries no quotes, so every tradable price reads as a dash — the model column is a valuation, not a fill."}{" "}
          Not investment advice.
        </p>
      </footer>
    </div>
  );
}
