"""Delayed option quotes from Cboe's public feed.

This is the second quote source, and the only one that needs no credentials
and no gateway process: a plain HTTPS GET that runs anywhere, including a
scheduled cloud job. It carries the whole chain -- bid, ask, sizes, implied
volatility, greeks, open interest -- plus the underlying's own market.

**These quotes are delayed.** Cboe publishes them on roughly a fifteen-minute
lag, and the payload timestamps itself, so a consumer can see exactly how old
the market it is looking at is. On a wide market that lag is the difference
between a number to reason about and a number to trade on, which is why
everything sourced here is labelled ``cboe`` and carries ``quotesAsOf``
rather than being passed off as live.

What it cannot supply is anything about *you*: no positions, no cash, no
fills. Those only come from the account, via TWS or a Flex statement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import requests

from .quotes import OptionQuote, Quote

log = logging.getLogger(__name__)

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

# Cboe's stated delay for this feed. Used only to describe the data, never to
# adjust it -- the payload's own timestamp is the authority on freshness.
NOMINAL_DELAY_MINUTES = 15

# The feed rejects requests without a browser-ish User-Agent.
_HEADERS = {"User-Agent": "sagix-balm/0.1 (+https://github.com/aummfi-bit/sagix-balm)"}


class CboeError(RuntimeError):
    """The feed was unreachable, or returned something unusable."""


@dataclass
class CboeChain:
    """One underlying's chain as published."""

    symbol: str
    spot: float
    underlying: Quote
    quotes: list[OptionQuote] = field(default_factory=list)
    iv30: float | None = None
    # When Cboe stamped the payload. Naive, US/Eastern, as published.
    as_of: datetime | None = None

    def marks(self) -> dict[tuple, Quote]:
        return {q.key(): q.quote for q in self.quotes}

    def strikes(self, window: float | None = None) -> list[float]:
        """Listed strikes, optionally within ``window`` either side of spot."""
        ks = {q.strike for q in self.quotes}
        if window is not None:
            lo, hi = self.spot * (1 - window), self.spot * (1 + window)
            ks = {k for k in ks if lo <= k <= hi}
        return sorted(ks)

    def expiries(self, kind: str = "put") -> list[date]:
        return sorted({q.expiry for q in self.quotes if q.kind == kind})

    def iv_for(self, kind: str, strike: float, expiry: date) -> float | None:
        q = self.marks_index.get((self.symbol, kind, strike, expiry))
        return q.iv if q else None

    @property
    def marks_index(self) -> dict[tuple, OptionQuote]:
        return {q.key(): q for q in self.quotes}


def parse_osi(osi: str) -> tuple[str, date, str, float]:
    """Split an OSI contract symbol.

    ``GLXY260814P00022000`` is root ``GLXY``, expiry 2026-08-14, a put, strike
    22.00. The root is variable width and the rest is fixed, so it is parsed
    from the right.
    """
    body = osi[-15:]
    root = osi[: -len(body)]
    if len(body) != 15 or body[6] not in "PC":
        raise ValueError(f"Not an OSI option symbol: {osi!r}")
    expiry = datetime.strptime(body[:6], "%y%m%d").date()
    kind = "put" if body[6] == "P" else "call"
    # Strike is eight digits in thousandths of a dollar.
    strike = int(body[7:]) / 1000.0
    return root, expiry, kind, strike


def _clean(value: Any) -> float | None:
    """Cboe writes an absent value as 0; keep zero only where it is meaningful."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v


def parse_chain(payload: dict[str, Any]) -> CboeChain:
    """Build a chain from a decoded Cboe payload."""
    try:
        data = payload["data"]
        symbol = data["symbol"]
        options = data["options"]
    except (KeyError, TypeError) as exc:
        raise CboeError(f"Unexpected payload shape: missing {exc}") from exc

    spot = _clean(data.get("current_price"))
    if not spot or spot <= 0:
        raise CboeError(f"{symbol}: no usable underlying price in the payload.")

    as_of = None
    raw_ts = payload.get("timestamp")
    if raw_ts:
        try:
            as_of = datetime.strptime(str(raw_ts), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            log.debug("Unparsed Cboe timestamp %r", raw_ts)

    quotes: list[OptionQuote] = []
    for row in options:
        osi = row.get("option")
        if not osi:
            continue
        try:
            _root, expiry, kind, strike = parse_osi(osi)
        except ValueError:
            log.debug("Skipping unparsable contract %r", osi)
            continue

        iv = _clean(row.get("iv"))
        quotes.append(
            OptionQuote(
                symbol=symbol,
                kind=kind,
                strike=strike,
                expiry=expiry,
                quote=Quote(
                    bid=_clean(row.get("bid")),
                    ask=_clean(row.get("ask")),
                    last=_clean(row.get("last_trade_price")),
                    close=_clean(row.get("prev_day_close")),
                ),
                # A zero implied vol is the feed saying it could not compute
                # one, not a claim that the option has no volatility.
                iv=iv if iv else None,
                delta=_clean(row.get("delta")),
                gamma=_clean(row.get("gamma")),
                theta=_clean(row.get("theta")),
                vega=_clean(row.get("vega")),
                open_interest=_clean(row.get("open_interest")),
                undPrice=spot,
            )
        )

    if not quotes:
        raise CboeError(f"{symbol}: the payload carried no parsable contracts.")

    iv30 = _clean(data.get("iv30"))
    return CboeChain(
        symbol=symbol,
        spot=spot,
        underlying=Quote(
            bid=_clean(data.get("bid")),
            ask=_clean(data.get("ask")),
            last=_clean(data.get("current_price")),
            close=_clean(data.get("prev_day_close")),
        ),
        quotes=quotes,
        # Published as a percentage.
        iv30=iv30 / 100.0 if iv30 else None,
        as_of=as_of,
    )


def fetch_chain(symbol: str, timeout: float = 20.0) -> CboeChain:
    """Download and parse one underlying's chain."""
    url = CBOE_URL.format(symbol=symbol.upper())
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        raise CboeError(f"{symbol}: {exc}") from exc

    if resp.status_code == 404:
        raise CboeError(f"{symbol}: no chain published at {url}.")
    if resp.status_code != 200:
        raise CboeError(f"{symbol}: HTTP {resp.status_code} from Cboe.")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise CboeError(f"{symbol}: response was not JSON.") from exc

    chain = parse_chain(payload)
    log.info(
        "%s: %d contracts from Cboe, spot %.2f, quoted as of %s",
        chain.symbol,
        len(chain.quotes),
        chain.spot,
        chain.as_of.isoformat(sep=" ") if chain.as_of else "unknown",
    )
    return chain
