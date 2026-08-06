"""Two-sided quotes, and the side you actually transact on.

A single "price" stops being meaningful the moment you have to trade it. You
buy at the ask and you sell at the bid, and on a name whose weekly puts quote
ten percent wide that difference is a large share of the edge the strategy is
trying to earn. Marking a long at the mid flatters it by half the spread;
marking a short liability at the mid understates what it costs to get out.
Both errors point the same way -- they make the position look better than it
can be liquidated for.

So every price in this package that stands for a real transaction carries a
side. Opening the anchor is a buy, priced at the ask. Selling a weekly is a
sell, priced at the bid. Closing either one reverses that.

When the book has no quote on the side in question the answer is ``None``.
Not the mid, not the last trade, not a model value dressed up as a fill.
"No quote" is a fact worth reporting; an invented price is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Side = Literal["buy", "sell"]


def closing_side(quantity: float) -> Side:
    """The side traded to flatten a position: sell a long, buy back a short."""
    return "sell" if quantity > 0 else "buy"


def opening_side(quantity: float) -> Side:
    """The side traded to establish a position."""
    return "buy" if quantity > 0 else "sell"


@dataclass
class Quote:
    """A market in one contract.

    ``last`` and ``close`` are kept for reference and for the underlying's spot,
    but they are deliberately unreachable through :meth:`executable`: neither
    is a price anyone will trade with you at right now.
    """

    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    close: float | None = None

    @property
    def mid(self) -> float | None:
        """Reference price only.

        Fine for the underlying's spot, which the model needs but nobody is
        transacting here, and fine as a display value. It is not what a fill
        costs, so it must never stand in for one -- use :meth:`executable`.
        """
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            return 0.5 * (self.bid + self.ask)
        return self.last if self.last is not None else self.close

    @property
    def two_sided(self) -> bool:
        """True when both sides are quoted, so either direction is priceable."""
        return (
            self.bid is not None and self.bid > 0 and self.ask is not None and self.ask > 0
        )

    @property
    def is_crossed(self) -> bool:
        """Bid above ask: stale or locked data, never a tradable market."""
        return self.two_sided and self.bid > self.ask  # type: ignore[operator]

    @property
    def spread(self) -> float | None:
        if not self.two_sided:
            return None
        return self.ask - self.bid  # type: ignore[operator]

    @property
    def spread_pct(self) -> float | None:
        m = self.mid
        s = self.spread
        if s is None or not m or m <= 0:
            return None
        return s / m

    def executable(self, side: Side) -> float | None:
        """What the trade actually prices at: pay the ask, receive the bid.

        Returns ``None`` when that side of the book is empty. A zero bid is
        the market saying nobody will pay anything, which is an absence of a
        price rather than a price of zero.
        """
        price = self.ask if side == "buy" else self.bid
        if price is None or price <= 0:
            return None
        return price

    def to_close(self, quantity: float) -> float | None:
        """Unit price to flatten ``quantity`` contracts."""
        return self.executable(closing_side(quantity))

    def to_open(self, quantity: float) -> float | None:
        """Unit price to establish ``quantity`` contracts."""
        return self.executable(opening_side(quantity))

    def as_dict(self) -> dict[str, Any]:
        return {
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "mid": self.mid,
            "spread": self.spread,
            "spreadPct": self.spread_pct,
            "twoSided": self.two_sided,
        }


def close_value(quote: Quote | None, quantity: float, multiplier: float = 100.0) -> float | None:
    """Signed cash from flattening a position at executable prices.

    Positive means money comes in (selling a long), negative means it goes out
    (buying back a short). ``None`` when that side is not quoted, which the
    caller must propagate rather than substitute a guess for.
    """
    if quote is None:
        return None
    price = quote.to_close(quantity)
    if price is None:
        return None
    return price * quantity * multiplier
