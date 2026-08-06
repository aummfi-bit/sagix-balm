"use client";

import { fmtDate } from "@/lib/pricing";

export type FeedState = "loading" | "live" | "stale" | "down";

/** "2026-08-06 17:26:15" (US/Eastern, as Cboe stamps it) → "17:26 ET". */
export function fmtClock(raw: string | null): string | null {
  if (!raw) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(raw);
  if (!m) return raw;
  return `${m[4]}:${m[5]} ET`;
}

/** Same stamp, with the date, for quotes that are not from today. */
export function fmtStamp(raw: string | null): string | null {
  if (!raw) return null;
  const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})/.exec(raw);
  if (!m) return raw;
  return `${fmtDate(m[1])} ${m[2]}:${m[3]} ET`;
}

/**
 * One line: is the feed answering, and how old are the prices on this page.
 *
 * Kept to a single row on purpose. The light is about the feed right now; the
 * timestamp is about the numbers being rendered. They are different facts, and
 * a green light beside week-old prices would quietly imply otherwise.
 */
export function QuoteStatus({
  state,
  quotesAsOf,
  delayMinutes,
  error,
}: {
  state: FeedState;
  quotesAsOf: string | null;
  delayMinutes: number;
  error?: string | null;
}) {
  const dot =
    state === "live"
      ? "bg-[var(--ok)]"
      : state === "down"
        ? "bg-[var(--bad)]"
        : state === "stale"
          ? "bg-[var(--warn)]"
          : "bg-[var(--text-dim)]";

  const headline =
    state === "live"
      ? "Cboe feed live"
      : state === "stale"
        ? "Cboe feed answered without a chain"
        : state === "down"
          ? `Cboe feed unreachable${error ? ` (${error})` : ""}`
          : "Reaching the Cboe feed…";

  const stamp = fmtStamp(quotesAsOf);

  return (
    <div className="-mx-4 border-b border-[var(--border)] bg-[var(--bg-light)] px-4 py-2.5 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
      <p className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--text-dim)]">
        <span
          className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`}
          aria-hidden
        />
        <span className="text-[var(--text)]">{headline}</span>
        <span aria-hidden>·</span>
        <span>
          {stamp ? `prices quoted ${stamp}` : "no quotes on this page"}
        </span>
        <span aria-hidden>·</span>
        <span className="text-[var(--warn)]">
          delayed ~{delayMinutes} min — indicative, not executable
        </span>
      </p>
    </div>
  );
}
