"use client";

import { useState } from "react";
import {
  holdingCost,
  holdingValue,
  newId,
  type Holdings,
  type OptionHolding,
} from "@/lib/holdings";
import type { Market } from "@/lib/markets";
import { fmtDate, num, usd, usdOrDash, type OptionKind } from "@/lib/pricing";

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5 font-mono text-sm text-[var(--text-strong)] outline-none focus:border-[var(--accent)]";

const labelClass =
  "text-[10px] uppercase tracking-[0.14em] text-[var(--text-dim)]";

/**
 * The cash set aside for assignment on short puts. Kept as its own panel,
 * above the holdings list, because it is the number a trader checks first —
 * before looking at what is actually held.
 */
export function CashPanel({
  holdings,
  onChange,
}: {
  holdings: Holdings;
  onChange: (next: Holdings) => void;
}) {
  return (
    <section className="space-y-3 rounded-md border border-[var(--border)] bg-[var(--bg-light)] p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--text-strong)]">
          Cash available to cover put shorts
        </h2>
        <span className="text-xs text-[var(--text-dim)]">
          Yours to enter — kept in this browser
        </span>
      </div>
      <div className="max-w-xs">
        <Field label="Cash available">
          <input
            type="number"
            step="0.01"
            value={holdings.cash}
            onChange={(e) =>
              onChange({ ...holdings, cash: Number(e.target.value) || 0 })
            }
            className={inputClass}
          />
        </Field>
      </div>
      <p className="text-xs text-[var(--text-dim)]">
        What you are holding back to cover assignment if a short put is
        exercised — not your whole account balance.
      </p>
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className={labelClass}>{label}</span>
      {children}
    </label>
  );
}

function AddOptionForm({
  market,
  onAdd,
  onCancel,
}: {
  market: Market;
  onAdd: (holding: OptionHolding) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<OptionKind>("put");
  const [side, setSide] = useState<"bought" | "sold">("sold");
  const [contracts, setContracts] = useState("1");
  const [strike, setStrike] = useState(
    String(Math.round(market.spot / market.strikeStep) * market.strikeStep),
  );
  const [expiry, setExpiry] = useState(market.tenors[0]?.expiry ?? "");
  const [price, setPrice] = useState("");

  const count = Math.abs(Number(contracts) || 0);
  const strikeNum = Number(strike);
  const valid = count > 0 && strikeNum > 0 && /^\d{4}-\d{2}-\d{2}$/.test(expiry);

  return (
    <div className="rounded-md border border-[var(--accent)]/40 bg-[var(--accent-faint)] p-3">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="Right">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as OptionKind)}
            className={inputClass}
          >
            <option value="put">Put</option>
            <option value="call">Call</option>
          </select>
        </Field>
        <Field label="Side">
          <select
            value={side}
            onChange={(e) => setSide(e.target.value as "bought" | "sold")}
            className={inputClass}
          >
            <option value="bought">Bought</option>
            <option value="sold">Sold</option>
          </select>
        </Field>
        <Field label="Contracts">
          <input
            type="number"
            min="1"
            step="1"
            value={contracts}
            onChange={(e) => setContracts(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Field label="Strike">
          <input
            type="number"
            step={market.strikeStep}
            value={strike}
            onChange={(e) => setStrike(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Field label="Expiry">
          <input
            type="date"
            value={expiry}
            onChange={(e) => setExpiry(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Field label="Fill price">
          <input
            type="number"
            step="0.01"
            min="0"
            placeholder="optional"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            className={inputClass}
          />
        </Field>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          disabled={!valid}
          onClick={() =>
            onAdd({
              id: newId(),
              kind,
              strike: strikeNum,
              expiry,
              quantity: side === "bought" ? count : -count,
              price: price === "" ? null : Number(price),
            })
          }
          className="rounded-md bg-[var(--accent)] px-3 py-1.5 text-sm font-semibold text-[#0d1117] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Add holding
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-dim)] transition hover:text-[var(--text-strong)]"
        >
          Cancel
        </button>
        <span className="text-xs text-[var(--text-dim)]">
          Stored in this browser only. Nothing is sent anywhere.
        </span>
      </div>
    </div>
  );
}

export function HoldingsPanel({
  market,
  holdings,
  onChange,
}: {
  market: Market;
  holdings: Holdings;
  onChange: (next: Holdings) => void;
}) {
  const [adding, setAdding] = useState(false);

  const stockValue = holdings.shares * market.spot;
  const sorted = [...holdings.options].sort(
    (a, b) =>
      a.expiry.localeCompare(b.expiry) || a.strike - b.strike || a.kind.localeCompare(b.kind),
  );

  return (
    <section className="space-y-4 rounded-md border border-[var(--border)] bg-[var(--bg-light)] p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--text-strong)]">
          Holdings · {market.symbol}
        </h2>
        <span className="text-xs text-[var(--text-dim)]">
          Yours to enter — kept in this browser
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-4 py-3">
          <div className="font-mono text-xl tabular-nums text-[var(--text-strong)]">
            {usd(market.spot)}
          </div>
          <div className="mt-1 text-[11px] uppercase tracking-[0.12em] text-[var(--text-dim)]">
            {market.symbol} price
          </div>
        </div>

        <Field label="Shares held">
          <input
            type="number"
            step="1"
            value={holdings.shares}
            onChange={(e) =>
              onChange({ ...holdings, shares: Number(e.target.value) || 0 })
            }
            className={inputClass}
          />
        </Field>

        <div className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-4 py-3">
          <div className="font-mono text-xl tabular-nums text-[var(--text-strong)]">
            {usd(stockValue, 0)}
          </div>
          <div className="mt-1 text-[11px] uppercase tracking-[0.12em] text-[var(--text-dim)]">
            Stock value
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            className="rounded-md border border-[var(--accent)]/50 px-3 py-1.5 text-sm font-medium text-[var(--accent)] transition hover:bg-[var(--accent-faint)]"
          >
            {adding ? "Close form" : "+ Add option"}
          </button>
          <span className="text-xs text-[var(--text-dim)]">
            {sorted.length === 0
              ? "No option holdings tracked."
              : `${sorted.length} option ${sorted.length === 1 ? "line" : "lines"}`}
          </span>
        </div>

        {adding ? (
          <AddOptionForm
            market={market}
            onAdd={(holding) => {
              onChange({ ...holdings, options: [...holdings.options, holding] });
              setAdding(false);
            }}
            onCancel={() => setAdding(false)}
          />
        ) : null}

        {sorted.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-[var(--border)]">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] bg-[var(--bg)]">
                  {[
                    "Position",
                    "Strike",
                    "Expiry",
                    "Contracts",
                    "Fill",
                    "Cost",
                    "Value now",
                    "",
                  ].map((h, i) => (
                    <th
                      key={h || i}
                      className={`px-3 py-2 text-[11px] font-medium uppercase tracking-[0.1em] text-[var(--text-dim)] ${
                        i === 0 || i === 7 ? "text-left" : "text-right"
                      }`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((o) => (
                  <tr
                    key={o.id}
                    className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--table-hover)]"
                  >
                    <td className="px-3 py-2 font-mono text-[var(--text)]">
                      <span
                        className={
                          o.quantity > 0 ? "text-[var(--accent)]" : "text-[var(--warn)]"
                        }
                      >
                        {o.quantity > 0 ? "Long" : "Short"}
                      </span>{" "}
                      {o.kind}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">
                      {num(o.strike, 2)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">
                      {fmtDate(o.expiry)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">
                      {num(Math.abs(o.quantity), 0)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">
                      {usdOrDash(o.price)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">
                      {usdOrDash(holdingCost(o), 0)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">
                      {usdOrDash(holdingValue(o, market), 0)}
                    </td>
                    <td className="px-3 py-2 text-left">
                      <button
                        type="button"
                        aria-label="Remove holding"
                        onClick={() =>
                          onChange({
                            ...holdings,
                            options: holdings.options.filter((x) => x.id !== o.id),
                          })
                        }
                        className="rounded border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-dim)] transition hover:border-[var(--bad)] hover:text-[var(--bad)]"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        <p className="text-xs text-[var(--text-dim)]">
          <strong className="text-[var(--text-strong)]">Value now</strong> closes
          the position at the side that would trade — a long sells into the bid,
          a short is bought back at the ask, both from the delayed feed. A dash
          means that side is not quoted.
        </p>
      </div>
    </section>
  );
}
