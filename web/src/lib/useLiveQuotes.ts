"use client";

import { useEffect, useState } from "react";
import type { Market, QuoteSides, Tenor } from "./markets";
import type { FeedState } from "@/components/QuoteStatus";

export type QuoteUpdate = {
  symbol: string;
  spot: number;
  quotesAsOf: string | null;
  quotes: Record<string, QuoteSides>;
  tenors: Tenor[];
};

/** Poll interval. The feed is fifteen minutes delayed; a minute is plenty. */
const REFRESH_MS = 60_000;

/**
 * Keep one underlying's quotes current.
 *
 * The desk used to render whatever snapshot happened to be committed. Since
 * the feed needs no credentials, the page can simply ask for fresh prices on a
 * timer instead — no button, no commit, no redeploy. The committed snapshot is
 * still what paints first, so the desk is never blank while this is in flight.
 */
type Feed = {
  /** Which symbol this result is about, so it is never read as another's. */
  symbol: string;
  update: QuoteUpdate | null;
  state: FeedState;
  error: string | null;
};

export function useLiveQuotes(symbol: string): Omit<Feed, "symbol"> {
  const [feed, setFeed] = useState<Feed>({
    symbol,
    update: null,
    state: "loading",
    error: null,
  });

  useEffect(() => {
    let live = true;

    async function load() {
      try {
        const res = await fetch(
          `/api/quotes?symbol=${encodeURIComponent(symbol)}`,
        );
        const body = await res.json();
        if (!live) return;
        if (!body.ok) {
          setFeed({
            symbol,
            update: null,
            state: "down",
            error: body.error ?? "unreachable",
          });
          return;
        }
        setFeed({
          symbol,
          update: {
            symbol: body.symbol,
            spot: body.spot,
            quotesAsOf: body.quotesAsOf ?? null,
            quotes: body.quotes ?? {},
            tenors: body.tenors ?? [],
          },
          state: Object.keys(body.quotes ?? {}).length ? "live" : "stale",
          error: null,
        });
      } catch (err) {
        if (!live) return;
        setFeed({
          symbol,
          update: null,
          state: "down",
          error: err instanceof Error ? err.message : "network error",
        });
      }
    }

    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [symbol]);

  // Everything about one poll — prices, light and error — is stamped with the
  // symbol it was for, so a ticker switch can never show one market's prices
  // under another's name, and an outage during a switch still reads as an
  // outage rather than as a load that never finished.
  if (feed.symbol !== symbol) {
    return { update: null, state: "loading", error: null };
  }
  return { update: feed.update, state: feed.state, error: feed.error };
}

/**
 * Lay fresh quotes over a market.
 *
 * Tenors are merged rather than replaced: the feed only carries expiries it is
 * currently quoting near the money, and the desk's selectors should not lose
 * an expiry just because this poll had no near-the-money volume in it.
 */
export function applyQuotes(market: Market, update: QuoteUpdate): Market {
  const tenors = new Map(market.tenors.map((t) => [t.expiry, t.iv]));
  for (const t of update.tenors) {
    if (t.iv > 0) tenors.set(t.expiry, t.iv);
  }

  return {
    ...market,
    spot: update.spot,
    quotes: update.quotes,
    quotesAsOf: update.quotesAsOf,
    tenors: [...tenors.entries()]
      .map(([expiry, iv]) => ({ expiry, iv }))
      .sort((a, b) => a.expiry.localeCompare(b.expiry)),
  };
}
