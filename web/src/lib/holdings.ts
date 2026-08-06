import type { Market } from "./markets";
import { executable, quoteFor, type OptionKind } from "./pricing";
import type { BalmSnapshot } from "./snapshot";

/**
 * What you are holding, per underlying.
 *
 * Two sources, deliberately kept apart. `sync` rows come from the account via
 * `balm sync` and are replaced wholesale on every refresh; `manual` rows are
 * typed into the desk and live only in this browser. Nothing here is ever sent
 * anywhere — the balm connection to IBKR is read-only and places no orders.
 */
export type HoldingSource = "sync" | "manual";

export type OptionHolding = {
  id: string;
  kind: OptionKind;
  strike: number;
  expiry: string;
  /** Signed: positive bought, negative sold. */
  quantity: number;
  /** Fill price per share, if known. */
  price: number | null;
  source: HoldingSource;
};

export type Holdings = {
  shares: number;
  cash: number;
  options: OptionHolding[];
  /** True once a sync has supplied the share count and cash. */
  fromSync: boolean;
};

export type HoldingsBySymbol = Record<string, Holdings>;

const STORAGE_KEY = "sagix-balm.holdings.v1";

export const EMPTY_HOLDINGS: Holdings = {
  shares: 0,
  cash: 0,
  options: [],
  fromSync: false,
};

export function holdingsFor(
  all: HoldingsBySymbol,
  symbol: string,
): Holdings {
  return all[symbol] ?? EMPTY_HOLDINGS;
}

export function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** Manual rows only: synced ones are re-derived, not remembered. */
function loadHoldings(): HoldingsBySymbol {
  if (typeof window === "undefined") return EMPTY_ALL;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as HoldingsBySymbol) : EMPTY_ALL;
  } catch {
    return EMPTY_ALL;
  }
}

function persist(all: HoldingsBySymbol): void {
  if (typeof window === "undefined") return;
  try {
    const manualOnly: HoldingsBySymbol = {};
    for (const [symbol, h] of Object.entries(all)) {
      manualOnly[symbol] = {
        ...h,
        options: h.options.filter((o) => o.source === "manual"),
      };
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(manualOnly));
  } catch {
    // A full or blocked localStorage is not worth breaking the desk over.
  }
}

/**
 * Holdings as an external store.
 *
 * They live in localStorage, which the server cannot see, so the desk reads
 * them through `useSyncExternalStore`: the server renders the empty snapshot
 * and the browser swaps in the stored one after hydration, without a
 * mismatch. The cached snapshot must be reference-stable between writes or
 * React will re-render forever.
 */
const EMPTY_ALL: HoldingsBySymbol = {};
let cache: HoldingsBySymbol | null = null;
const listeners = new Set<() => void>();

export function subscribeHoldings(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

export function getHoldings(): HoldingsBySymbol {
  if (cache === null) cache = loadHoldings();
  return cache;
}

export function getServerHoldings(): HoldingsBySymbol {
  return EMPTY_ALL;
}

export function setHoldings(next: HoldingsBySymbol): void {
  cache = next;
  persist(next);
  for (const listener of listeners) listener();
}

/**
 * Fold a fresh snapshot into the current holdings.
 *
 * Synced rows are replaced outright so a closed position disappears rather
 * than lingering; manual rows are left alone. Share count and cash are only
 * overwritten when the snapshot actually carries them, so a model refresh does
 * not wipe numbers the user typed in.
 */
export function mergeSnapshotHoldings(
  current: HoldingsBySymbol,
  snap: BalmSnapshot,
): HoldingsBySymbol {
  const next: HoldingsBySymbol = { ...current };
  const cash = snap.account?.TotalCashValue;

  for (const u of snap.underlyings) {
    const existing = next[u.symbol] ?? EMPTY_HOLDINGS;
    const positions = u.positions ?? [];
    const isLive = u.source === "live";

    const stock = positions.find((p) => p.secType === "STK");
    const options: OptionHolding[] = positions
      .filter(
        (p) =>
          p.secType === "OPT" &&
          (p.kind === "put" || p.kind === "call") &&
          p.strike != null &&
          p.expiry != null,
      )
      .map((p) => ({
        id: `sync-${u.symbol}-${p.kind}-${p.strike}-${p.expiry}`,
        kind: p.kind as OptionKind,
        strike: p.strike as number,
        expiry: p.expiry as string,
        quantity: p.quantity,
        // IBKR reports avgCost per contract; the desk works per share.
        price: p.avgCost ? Math.abs(p.avgCost) / (p.multiplier || 100) : null,
        source: "sync" as const,
      }));

    next[u.symbol] = {
      shares: stock ? stock.quantity : existing.shares,
      cash: cash ?? existing.cash,
      options: [
        ...options,
        ...existing.options.filter((o) => o.source === "manual"),
      ],
      fromSync: isLive && (stock != null || cash != null),
    };
  }
  return next;
}

/**
 * What closing this option would pay, at the side that would trade: a long
 * sells into the bid, a short is bought back at the ask. `null` when that side
 * is unquoted — the position has a size, but not a price.
 */
export function holdingValue(
  holding: OptionHolding,
  market: Market,
): number | null {
  const quote = quoteFor(market, holding.kind, holding.expiry, holding.strike);
  const price = executable(quote, holding.quantity > 0 ? "sell" : "buy");
  return price == null ? null : price * holding.quantity * 100;
}

export function holdingCost(holding: OptionHolding): number | null {
  return holding.price == null ? null : holding.price * holding.quantity * 100;
}
