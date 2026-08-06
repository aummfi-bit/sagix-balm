import {
  DEFAULTS,
  MARKETS,
  type Market,
  type QuoteSides,
  type Selection,
  type Tenor,
} from "./markets";
import { quoteKey } from "./pricing";

export type BalmSnapshot = {
  schemaVersion: number;
  generatedAt: string;
  asof: string;
  account?: Record<string, number>;
  /** Where the prices came from: "cboe" for the delayed public feed. */
  quoteSource?: string;
  quotesDelayed?: boolean;
  quoteDelayMinutes?: number;
  /** Feed's own timestamp, naive US/Eastern, as published. */
  quotesAsOf?: string | null;
  underlyings: Array<{
    symbol: string;
    label?: string;
    source?: string;
    spot: number;
    volBeta?: number;
    quotesAsOf?: string | null;
    positions?: Array<{
      symbol: string;
      secType: string;
      quantity: number;
      avgCost: number;
      multiplier: number;
      kind?: string | null;
      strike?: number | null;
      expiry?: string | null;
      exitPrice?: number | null;
    }>;
    structure?: {
      contracts?: number;
      legs?: Array<{
        label?: string;
        strike: number;
        expiry: string;
        iv: number;
        bid?: number | null;
        ask?: number | null;
      }>;
    };
    termStructure?: {
      shortIv?: number;
      anchorIv?: number;
    };
    chain?: Array<{
      expiry: string;
      strike: number;
      iv: number | null;
      kind?: string;
      bid?: number | null;
      ask?: number | null;
    }>;
  }>;
};

type SnapshotUnderlying = BalmSnapshot["underlyings"][number];

/**
 * Collect the two-sided markets a live sync carried.
 *
 * A model snapshot has no book behind it and yields nothing, which is the
 * correct answer: the desk then shows model values labelled as such instead of
 * implying prices that were never quoted.
 */
function quotesFrom(u: SnapshotUnderlying): Record<string, QuoteSides> | undefined {
  const out: Record<string, QuoteSides> = {};
  const add = (
    kind: string,
    expiry: string,
    strike: number,
    bid?: number | null,
    ask?: number | null,
  ) => {
    if (bid == null && ask == null) return;
    if (kind !== "put" && kind !== "call") return;
    out[quoteKey(kind, expiry, strike)] = { bid: bid ?? null, ask: ask ?? null };
  };

  for (const q of u.chain ?? []) add(q.kind ?? "put", q.expiry, q.strike, q.bid, q.ask);
  // The traded legs carry their own quote even when the chain is absent. The
  // tracked campaign is a put calendar, so its legs are puts.
  for (const l of u.structure?.legs ?? [])
    add("put", l.expiry, l.strike, l.bid, l.ask);

  return Object.keys(out).length ? out : undefined;
}

export type DeskData = {
  asof: string;
  generatedAt: string;
  command: string;
  markets: Market[];
  selections: Record<string, Selection>;
  /** How the prices in these markets were obtained, and how stale they are. */
  quoteSource: string | null;
  quotesDelayed: boolean;
  quoteDelayMinutes: number | null;
  quotesAsOf: string | null;
};

function sourceLabel(source: string | undefined): string {
  if (source === "live") return "balm sync";
  if (source === "cboe") return "balm quotes · Cboe delayed";
  return "balm plan";
}

function mergeTenors(
  base: Tenor[],
  patches: Array<{ expiry: string; iv: number }>,
): Tenor[] {
  const map = new Map(base.map((t) => [t.expiry, t.iv]));
  for (const p of patches) {
    if (p.iv > 0) map.set(p.expiry, p.iv);
  }
  return [...map.entries()]
    .map(([expiry, iv]) => ({ expiry, iv }))
    .sort((a, b) => a.expiry.localeCompare(b.expiry));
}

/** Fold a balm snapshot into the desk's market/selection state. */
export function deskDataFromSnapshot(
  snap: BalmSnapshot,
  command: string,
): DeskData {
  const selections: Record<string, Selection> = { ...DEFAULTS };
  const markets: Market[] = MARKETS.map((m) => {
    const u = snap.underlyings.find((x) => x.symbol === m.symbol);
    if (!u) return m;

    const legs = u.structure?.legs ?? [];
    const anchor = legs.find((l) => l.label === "anchor") ?? legs[0];
    const short =
      legs.find((l) => l.label === "short") ??
      legs.find((l) => l !== anchor);

    const patches: Array<{ expiry: string; iv: number }> = [];
    if (anchor) patches.push({ expiry: anchor.expiry, iv: anchor.iv });
    if (short) patches.push({ expiry: short.expiry, iv: short.iv });
    if (u.termStructure?.anchorIv && anchor) {
      patches.push({
        expiry: anchor.expiry,
        iv: u.termStructure.anchorIv,
      });
    }
    if (u.termStructure?.shortIv && short) {
      patches.push({ expiry: short.expiry, iv: u.termStructure.shortIv });
    }

    // Prefer ATM-ish chain IVs when present (live sync).
    if (u.chain?.length) {
      const byExpiry = new Map<string, number[]>();
      for (const q of u.chain) {
        if (q.iv == null || q.iv <= 0) continue;
        if (Math.abs(q.strike - u.spot) / u.spot > 0.08) continue;
        const list = byExpiry.get(q.expiry) ?? [];
        list.push(q.iv);
        byExpiry.set(q.expiry, list);
      }
      for (const [expiry, ivs] of byExpiry) {
        const mid = ivs.sort((a, b) => a - b)[Math.floor(ivs.length / 2)];
        patches.push({ expiry, iv: mid });
      }
    }

    if (anchor && short) {
      selections[m.symbol] = {
        anchorExpiry: anchor.expiry,
        anchorStrike: anchor.strike,
        shortExpiry: short.expiry,
        shortStrike: short.strike,
        contracts: u.structure?.contracts ?? 1,
      };
    }

    return {
      ...m,
      label: u.label ?? m.label,
      spot: u.spot,
      volBeta: u.volBeta ?? m.volBeta,
      source: `${sourceLabel(u.source)} · ${snap.generatedAt}`,
      tenors: mergeTenors(m.tenors, patches),
      // Always reassigned, so a model refresh clears stale quotes rather than
      // leaving yesterday's book attached to today's prices.
      quotes: quotesFrom(u),
      quotesAsOf: u.quotesAsOf ?? null,
    };
  });

  // Include any underlyings from the snapshot that aren't in the seed list.
  for (const u of snap.underlyings) {
    if (markets.some((m) => m.symbol === u.symbol)) continue;
    const legs = u.structure?.legs ?? [];
    const anchor = legs.find((l) => l.label === "anchor") ?? legs[0];
    const short =
      legs.find((l) => l.label === "short") ??
      legs.find((l) => l !== anchor);
    if (!anchor || !short) continue;
    markets.push({
      symbol: u.symbol,
      label: u.label ?? u.symbol,
      spot: u.spot,
      strikeStep: u.spot < 25 ? 0.5 : 1,
      volBeta: u.volBeta ?? 1,
      source: `balm · ${snap.generatedAt}`,
      tenors: [
        { expiry: short.expiry, iv: short.iv },
        { expiry: anchor.expiry, iv: anchor.iv },
      ],
      quotes: quotesFrom(u),
      quotesAsOf: u.quotesAsOf ?? null,
    });
    selections[u.symbol] = {
      anchorExpiry: anchor.expiry,
      anchorStrike: anchor.strike,
      shortExpiry: short.expiry,
      shortStrike: short.strike,
      contracts: u.structure?.contracts ?? 1,
    };
  }

  return {
    asof: snap.asof,
    generatedAt: snap.generatedAt,
    command,
    markets,
    selections,
    quoteSource: snap.quoteSource ?? null,
    quotesDelayed: snap.quotesDelayed ?? false,
    quoteDelayMinutes: snap.quoteDelayMinutes ?? null,
    quotesAsOf: snap.quotesAsOf ?? null,
  };
}
