import type { Market } from "./markets";
import { executable, quoteFor, type OptionKind } from "./pricing";

/**
 * What you are holding, per underlying.
 *
 * Typed in and kept in this browser. There is no broker connection to read
 * positions from and nothing is ever sent anywhere: the desk knows what you
 * tell it, and prices it off the public quote feed.
 */
export type OptionHolding = {
  id: string;
  kind: OptionKind;
  strike: number;
  expiry: string;
  /** Signed: positive bought, negative sold. */
  quantity: number;
  /** Fill price per share, if known. */
  price: number | null;
};

export type Holdings = {
  shares: number;
  cash: number;
  options: OptionHolding[];
};

export type HoldingsBySymbol = Record<string, Holdings>;

const STORAGE_KEY = "sagix-balm.holdings.v1";

export const EMPTY_HOLDINGS: Holdings = { shares: 0, cash: 0, options: [] };

export function holdingsFor(
  all: HoldingsBySymbol,
  symbol: string,
): Holdings {
  return all[symbol] ?? EMPTY_HOLDINGS;
}

export function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

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
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
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
