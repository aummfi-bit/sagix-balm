"use client";

import { useEffect, useState } from "react";

type Status =
  | { state: "checking" }
  | { state: "ok"; feedAsOf: string | null }
  | { state: "down"; error: string };

/** "2026-08-06 17:26:15" (US/Eastern, as Cboe stamps it) → "06 Aug 17:26 ET". */
function fmtFeedTime(raw: string | null): string | null {
  if (!raw) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(raw);
  if (!m) return raw;
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${m[3]} ${months[Number(m[2]) - 1]} ${m[4]}:${m[5]} ET`;
}

/**
 * One line on whether the delayed-quote feed is up, and how old the prices on
 * this page are.
 *
 * The light is about reachability of the feed right now. The timestamp is
 * about the snapshot the desk is actually rendering, which is a different
 * thing and is stated separately — a green light on a week-old snapshot would
 * otherwise read as "these prices are current".
 */
export function QuoteStatus({
  symbol,
  quoteSource,
  quotesAsOf,
  delayMinutes,
}: {
  symbol: string;
  quoteSource: string | null;
  quotesAsOf: string | null;
  delayMinutes: number | null;
}) {
  const [status, setStatus] = useState<Status>({ state: "checking" });

  useEffect(() => {
    let live = true;
    fetch(`/api/quotes/status?symbol=${encodeURIComponent(symbol)}`)
      .then((r) => r.json())
      .then((body) => {
        if (!live) return;
        setStatus(
          body.ok
            ? { state: "ok", feedAsOf: body.feedAsOf ?? null }
            : { state: "down", error: body.error ?? "unreachable" },
        );
      })
      .catch((err) => {
        if (live) {
          setStatus({
            state: "down",
            error: err instanceof Error ? err.message : "network error",
          });
        }
      });
    return () => {
      live = false;
    };
  }, [symbol]);

  const dot =
    status.state === "ok"
      ? "bg-[var(--ok)]"
      : status.state === "down"
        ? "bg-[var(--bad)]"
        : "bg-[var(--text-dim)]";

  const link =
    status.state === "ok"
      ? `Cboe feed reachable${
          fmtFeedTime(status.feedAsOf)
            ? `, publishing ${fmtFeedTime(status.feedAsOf)}`
            : ""
        }`
      : status.state === "down"
        ? `Cboe feed unreachable (${status.error})`
        : "Checking the Cboe feed…";

  const shown =
    quoteSource === "cboe"
      ? `prices on this page quoted ${fmtFeedTime(quotesAsOf) ?? "at an unknown time"}`
      : "this page carries no quotes — every tradable price reads as a dash";

  return (
    <div className="-mx-4 border-b border-[var(--border)] bg-[var(--bg-light)] px-4 py-2.5 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
      <p className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--text-dim)]">
        <span
          className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`}
          aria-hidden
        />
        <span className="text-[var(--text)]">{link}</span>
        <span aria-hidden>·</span>
        <span>{shown}</span>
        <span aria-hidden>·</span>
        <span className="text-[var(--warn)]">
          delayed ~{delayMinutes ?? 15} min — indicative, not executable
        </span>
      </p>
    </div>
  );
}
