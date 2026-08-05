"use client";

import { useMemo, useState } from "react";
import {
  ASOF,
  CAPTURE_RATE,
  DEFAULTS,
  MARKETS,
  RATE,
  type Selection,
} from "@/lib/markets";
import {
  buildModel,
  daysFrom,
  fmtDate,
  ivFor,
  ladder,
  pct,
  putGreeks,
  signed,
  usd,
  type Model,
} from "@/lib/pricing";
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
      <span className="text-[11px] uppercase tracking-[0.14em] text-[var(--muted)]">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-[var(--stroke)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--ink)] outline-none focus:border-[var(--accent)]"
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
      ? "text-[var(--good)]"
      : tone === "warn"
        ? "text-[var(--warn)]"
        : tone === "bad"
          ? "text-[var(--bad)]"
          : "text-[var(--ink)]";
  return (
    <div className="rounded-lg border border-[var(--stroke)] bg-[var(--panel)]/70 px-4 py-3">
      <div
        className={`font-mono text-xl tabular-nums tracking-tight ${color}`}
      >
        {value}
      </div>
      <div className="mt-1 text-[11px] uppercase tracking-[0.12em] text-[var(--muted)]">
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
  const { market, sel } = model;
  const strikes = ladder(market.spot, market.strikeStep);
  const longTenors = market.tenors.filter((t) => daysFrom(t.expiry) > 60);
  const shortTenors = market.tenors.filter(
    (t) => daysFrom(t.expiry) <= 60 && daysFrom(t.expiry) > 0,
  );

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <SelectField
        label="Anchor expiry"
        value={sel.anchorExpiry}
        onChange={(v) => onChange({ ...sel, anchorExpiry: v })}
        options={longTenors.map((t) => ({
          value: t.expiry,
          label: `${fmtDate(t.expiry)} · ${daysFrom(t.expiry)}d · ${pct(t.iv, 0)} IV`,
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
          label: `${fmtDate(t.expiry)} · ${daysFrom(t.expiry)}d · ${pct(t.iv, 0)} IV`,
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
    good: "bg-[var(--good)]",
    warn: "bg-[var(--warn)]",
    bad: "bg-[var(--bad)]",
    info: "bg-[var(--accent)]",
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--stroke)]">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-[var(--stroke)] bg-[var(--panel)]">
            {headers.map((h, i) => (
              <th
                key={h}
                className={`px-3 py-2 text-[11px] font-medium uppercase tracking-[0.1em] text-[var(--muted)] ${
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
              className="border-b border-[var(--stroke)]/70 last:border-0 odd:bg-[var(--panel)]/40"
            >
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className={`px-3 py-2 font-mono tabular-nums text-[var(--ink)] ${
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
  const { sel, anchor, short } = model;
  const mult = 100 * sel.contracts;
  return (
    <DataTable
      headers={[
        "Leg",
        "Strike",
        "Expiry",
        "DTE",
        "IV",
        "Price",
        "Delta",
        "Theta $/day",
        "Vega $/pt",
      ]}
      alignRight={[
        false,
        true,
        false,
        true,
        true,
        true,
        true,
        true,
        true,
      ]}
      rowTone={["info", "warn", undefined]}
      rows={[
        [
          "Anchor (long)",
          `${sel.anchorStrike.toFixed(2)}P`,
          fmtDate(sel.anchorExpiry),
          `${model.anchorDte}d`,
          pct(model.anchorIv, 0),
          usd(anchor.price),
          signed(anchor.delta, 3),
          signed(anchor.theta * mult, 2),
          signed(anchor.vega * mult, 2),
        ],
        [
          "Weekly (short)",
          `${sel.shortStrike.toFixed(2)}P`,
          fmtDate(sel.shortExpiry),
          `${model.shortDte}d`,
          pct(model.shortIv, 0),
          usd(short.price),
          signed(-short.delta, 3),
          signed(-short.theta * mult, 2),
          signed(-short.vega * mult, 2),
        ],
        [
          "Net",
          "",
          "",
          "",
          "",
          usd(model.anchorCost / mult - short.price),
          signed(model.netDelta / mult, 3),
          signed(model.netTheta, 2),
          signed(model.netVega, 2),
        ],
      ]}
    />
  );
}

function StrikeLadder({ model }: { model: Model }) {
  const { market, sel } = model;
  const years = model.shortDte / 365;
  const strikes = ladder(market.spot, market.strikeStep, 6).filter(
    (k) => k <= market.spot * 1.06,
  );
  const rows = strikes
    .slice()
    .reverse()
    .map((k) => {
      const g = putGreeks(market.spot, k, years, model.shortIv);
      const income = g.price * 100 * sel.contracts;
      const weeks = income > 0 ? model.anchorCost / income / CAPTURE_RATE : 0;
      const breakeven = k - g.price;
      return {
        k,
        cells: [
          k.toFixed(2),
          usd(g.price),
          signed(g.delta, 3),
          g.gamma.toFixed(4),
          signed(g.theta * 100 * sel.contracts, 2),
          usd(breakeven),
          pct(breakeven / market.spot - 1, 1),
          weeks > 0 ? `${weeks.toFixed(1)} wk` : "-",
        ],
      };
    });

  const tones = rows.map((r) => {
    const d = Math.abs(
      putGreeks(market.spot, r.k, years, model.shortIv).delta,
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
          "Credit",
          "Delta",
          "Gamma",
          "Theta $/day",
          "Breakeven",
          "vs spot",
          "Payoff pace",
        ]}
        alignRight={[true, true, true, true, true, true, true, true]}
        rows={rows.map((r) => r.cells)}
        rowTone={tones}
      />
      <p className="text-sm text-[var(--muted)]">
        {fmtDate(sel.shortExpiry)} puts at {pct(model.shortIv, 0)} IV. Green
        rows sit in the 0.30–0.50 delta band; red is past 0.55; amber is your
        current short.
      </p>
    </div>
  );
}

function RollChecklist({ model }: { model: Model }) {
  const absDelta = Math.abs(model.short.delta);
  const itm = model.spot < model.sel.shortStrike;
  const weekday = new Date(`${ASOF}T12:00:00Z`).getUTCDay();
  const pastCutoff = weekday > 3 || weekday === 0;

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
          : `Short delta ${absDelta.toFixed(2)} past 0.55. Roll down.`,
    },
    {
      ok: !itm,
      label: "Assignment risk",
      detail: itm
        ? `Spot ${usd(model.spot)} below ${usd(model.sel.shortStrike)} strike.`
        : `Spot ${usd(model.spot)} above ${usd(model.sel.shortStrike)} — OTM.`,
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
  ];

  return (
    <DataTable
      headers={["Check", "Status", "Detail"]}
      rows={checks.map((c) => [c.label, c.ok ? "Clear" : "Act", c.detail])}
      rowTone={checks.map((c) => (c.ok ? "good" : "bad"))}
    />
  );
}

export function PutCalendarDesk() {
  const [active, setActive] = useState("GLXY");
  const [selections, setSelections] =
    useState<Record<string, Selection>>(DEFAULTS);

  const market = MARKETS.find((m) => m.symbol === active) ?? MARKETS[0];
  const sel = selections[market.symbol] ?? DEFAULTS[market.symbol];
  const model = useMemo(() => buildModel(market, sel), [market, sel]);

  const update = (next: Selection) =>
    setSelections((prev) => ({ ...prev, [market.symbol]: next }));

  const weeks = Math.min(Math.ceil(model.weeksRealistic), 20);
  const perWeek = model.weeklyIncome * CAPTURE_RATE;

  const termTone =
    model.shape === "backwardation"
      ? "border-[var(--good)]/40 bg-[var(--good)]/10"
      : model.shape === "contango"
        ? "border-[var(--warn)]/40 bg-[var(--warn)]/10"
        : "border-[var(--stroke)] bg-[var(--panel)]";

  const termBody =
    model.shape === "backwardation"
      ? `You sell ${pct(model.shortIv, 0)} vol on the weekly and buy ${pct(model.anchorIv, 0)} on the anchor. The surface pays you ${pct(model.ivEdge, 1)} to run this calendar.`
      : model.shape === "contango"
        ? `You sell ${pct(model.shortIv, 0)} vol on the weekly but pay ${pct(model.anchorIv, 0)} for the anchor — a ${pct(Math.abs(model.ivEdge), 1)} structural headwind.`
        : `Front and long-dated vol are within ${pct(Math.abs(model.ivEdge), 1)}. No structural edge either way.`;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-10 px-4 py-10 sm:px-6 lg:px-8">
      <header className="space-y-4">
        <p className="font-[family-name:var(--font-display)] text-sm tracking-[0.28em] uppercase text-[var(--accent)]">
          sagix balm
        </p>
        <h1 className="font-[family-name:var(--font-display)] text-4xl leading-tight tracking-tight text-[var(--ink)] sm:text-5xl">
          Put calendar desk
        </h1>
        <p className="max-w-2xl text-[var(--muted)]">
          Long-dated put anchor financed by weekly short puts. Modeled as of{" "}
          {fmtDate(ASOF)} at a {pct(RATE, 0)} risk-free rate. Adjust strikes
          and expiries — greeks and scenarios recompute live.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {MARKETS.map((m) => (
          <button
            key={m.symbol}
            type="button"
            onClick={() => setActive(m.symbol)}
            className={`rounded-md px-3 py-1.5 font-mono text-sm transition ${
              m.symbol === active
                ? "bg-[var(--accent)] text-[var(--bg)]"
                : "border border-[var(--stroke)] text-[var(--muted)] hover:text-[var(--ink)]"
            }`}
          >
            {m.symbol}
          </button>
        ))}
        <span className="ml-auto text-xs text-[var(--muted)]">
          {market.label} · {market.source}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat value={usd(model.spot)} label={`${market.symbol} spot`} />
        <Stat value={usd(model.anchorCost, 0)} label="Anchor debit" />
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
          value={`${model.weeksRealistic.toFixed(1)} wk`}
          label="Weeks to pay off"
        />
      </div>

      <div className={`rounded-lg border px-4 py-3 ${termTone}`}>
        <div className="text-sm font-medium text-[var(--ink)]">
          Volatility term structure: {model.shape}
        </div>
        <p className="mt-1 text-sm text-[var(--muted)]">{termBody}</p>
      </div>

      <section className="space-y-4">
        <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
          Structure
        </h2>
        <Controls model={model} onChange={update} />
        <LegsTable model={model} />
      </section>

      <section className="space-y-4">
        <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
          Paying off the anchor
        </h2>
        <div className="space-y-2">
          <div className="flex justify-between text-xs text-[var(--muted)]">
            <span>
              {model.weeksRealistic.toFixed(1)} weekly sales to cover the
              anchor
            </span>
            <span>
              {usd(perWeek, 0)}/week net vs {usd(model.anchorCost, 0)} debit
            </span>
          </div>
          <div className="flex h-3 overflow-hidden rounded-full bg-[var(--stroke)]/50">
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
          <p className="text-sm text-[var(--muted)]">
            Each segment is one weekly sale at {usd(model.weeklyPremium)} per
            share, kept at {pct(CAPTURE_RATE, 0)} after early closes and
            commissions. Gross count is {model.weeksGross.toFixed(1)} weeks at
            full capture.
          </p>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
          Profit and loss across spot moves
        </h2>
        <div className="rounded-lg border border-[var(--stroke)] bg-[var(--panel)]/50 p-4">
          <ScenarioChart
            categories={model.moves.map(
              (m) => `${m >= 0 ? "+" : ""}${(m * 100).toFixed(0)}%`,
            )}
            series={[
              {
                name: "1 day",
                data: model.scenarioRow(1),
                color: "var(--muted)",
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
          <div className="mt-3 flex flex-wrap gap-4 text-xs text-[var(--muted)]">
            <span>
              <i
                className="mr-1.5 inline-block h-2 w-2 rounded-full"
                style={{ background: "var(--muted)" }}
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
        <p className="text-sm text-[var(--muted)]">
          Vol shocked with spot at beta {market.volBeta}, damped by √T across
          the term structure — the short leg reacts far more than the anchor.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
          This week&apos;s strike ladder
        </h2>
        <StrikeLadder model={model} />
      </section>

      <section className="space-y-4">
        <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
          Roll checklist
        </h2>
        <RollChecklist model={model} />
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-[var(--stroke)] bg-[var(--panel)]/60 p-5">
          <h3 className="mb-3 font-[family-name:var(--font-display)] text-lg">
            Weekly operating loop
          </h3>
          <ul className="space-y-2 text-sm text-[var(--muted)]">
            <li>
              <strong className="text-[var(--ink)]">Monday.</strong> Mark the
              anchor and open short. Update banked premium.
            </li>
            <li>
              <strong className="text-[var(--ink)]">Tue / Wed.</strong> Close
              once ~80% of credit is captured. Never carry into Thursday.
            </li>
            <li>
              <strong className="text-[var(--ink)]">Same session.</strong> Sell
              next weekly by target delta, not fixed distance from spot.
            </li>
            <li>
              <strong className="text-[var(--ink)]">Always.</strong> Mid-price
              limit orders — wide spreads are a tax on payoff horizon.
            </li>
          </ul>
        </div>
        <div className="rounded-lg border border-[var(--stroke)] bg-[var(--panel)]/60 p-5">
          <h3 className="mb-3 font-[family-name:var(--font-display)] text-lg">
            What actually goes wrong
          </h3>
          <ul className="space-y-2 text-sm text-[var(--muted)]">
            <li>
              <strong className="text-[var(--ink)]">Sharp rally.</strong> Worst
              case: anchor loses on delta and vega while credits shrink.
            </li>
            <li>
              <strong className="text-[var(--ink)]">Fast crash.</strong> Short
              IV spikes more than the anchor; assignment risk rises.
            </li>
            <li>
              <strong className="text-[var(--ink)]">Grind lower.</strong> Best
              case — roll strikes down, collect, anchor gains.
            </li>
            <li>
              <strong className="text-[var(--ink)]">Assignment.</strong>{" "}
              Becomes a married put — legitimate, but capital-heavy.
            </li>
          </ul>
        </div>
      </section>

      <footer className="border-t border-[var(--stroke)] pt-6 text-xs text-[var(--muted)]">
        <p>
          Model prices assume mid fills. Not investment advice. Front IV for
          the selected short is {pct(ivFor(market, sel.shortExpiry), 0)};
          European Black-Scholes for responsiveness — the Python package also
          prices American early exercise for assignment risk.
        </p>
      </footer>
    </div>
  );
}
