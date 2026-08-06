"""Campaign accounting: how far the weekly premium has paid off the anchor.

A "campaign" is one long-dated put plus the running series of short puts sold
against it. The headline number is the recovery ratio -- realized short premium
divided by the debit paid for the anchor. Everything is tracked in signed cash
flows so that rolls, partial closes, assignments and commissions all fold into
the same arithmetic without special cases.

Sign convention throughout: cash flow is positive when money enters the
account. Buying costs money, selling raises it, commissions always subtract.

Still-open legs are valued at what it would cost to *get out of them*, not at
the mid. The anchor is a long, so it liquidates into the bid; an open short
weekly has to be bought back at the ask. Marking both at the mid overstates
the campaign by half a spread on each leg, in the flattering direction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .pricing import OptionKind
from .quotes import Quote, close_value


@dataclass
class Trade:
    """A single execution, normalized from Flex or entered by hand."""

    symbol: str
    trade_date: date
    quantity: float  # signed: positive bought, negative sold
    price: float
    multiplier: float = 100.0
    commission: float = 0.0  # negative, as IBKR reports it
    kind: OptionKind | None = None  # None means the underlying itself
    strike: float | None = None
    expiry: date | None = None
    notes: str = ""
    trade_id: str = ""

    @property
    def cash_flow(self) -> float:
        return -(self.quantity * self.price * self.multiplier) + self.commission

    @property
    def is_option(self) -> bool:
        return self.kind is not None and self.strike is not None and self.expiry is not None

    def contract_key(self) -> tuple:
        return (self.symbol, self.kind, self.strike, self.expiry)

    def was_assigned(self) -> bool:
        # IBKR note codes: A = assignment, Ex = exercise, Ep = expired.
        codes = {c.strip() for c in self.notes.replace(",", ";").split(";")}
        return bool(codes & {"A", "Ex"})


@dataclass
class ContractLedger:
    """All trades in one option contract, netted."""

    symbol: str
    kind: OptionKind
    strike: float
    expiry: date
    trades: list[Trade] = field(default_factory=list)

    @property
    def net_quantity(self) -> float:
        return sum(t.quantity for t in self.trades)

    @property
    def net_cash(self) -> float:
        return sum(t.cash_flow for t in self.trades)

    @property
    def commissions(self) -> float:
        return sum(t.commission for t in self.trades)

    @property
    def opened_on(self) -> date:
        return min(t.trade_date for t in self.trades)

    @property
    def assigned(self) -> bool:
        return any(t.was_assigned() for t in self.trades)

    def is_settled(self, asof: date) -> bool:
        """Flat, or past expiration so the position can no longer change."""
        return abs(self.net_quantity) < 1e-9 or self.expiry < asof


@dataclass
class CampaignMetrics:
    symbol: str
    asof: date

    anchor_strike: float | None
    anchor_expiry: date | None
    anchor_quantity: float
    anchor_debit: float  # positive number of dollars paid
    anchor_mark: float | None  # proceeds from selling the anchor at the bid

    realized_premium: float  # net cash banked from settled short legs
    open_short_credit: float  # cash taken in on short legs still open
    open_short_liability: float | None  # cost to buy them back at the ask
    commissions: float

    weeks_active: float
    settled_short_legs: int
    losing_short_legs: int
    assignments: int

    @property
    def recovery_pct(self) -> float | None:
        """Share of the anchor debit already paid for by realized premium."""
        if self.anchor_debit <= 0:
            return None
        return self.realized_premium / self.anchor_debit

    @property
    def remaining_debit(self) -> float:
        return max(self.anchor_debit - self.realized_premium, 0.0)

    @property
    def avg_weekly_capture(self) -> float | None:
        if self.weeks_active <= 0 or self.settled_short_legs == 0:
            return None
        return self.realized_premium / self.weeks_active

    @property
    def weeks_to_payoff(self) -> float | None:
        """Weeks of further selling needed, extrapolating the realized rate.

        This uses the rate actually achieved so far, including losing weeks,
        rather than a theoretical full-premium capture. It is the honest
        estimate and is normally much longer than the naive one.
        """
        rate = self.avg_weekly_capture
        if rate is None or rate <= 0:
            return None
        return self.remaining_debit / rate

    @property
    def net_liquidation(self) -> float | None:
        """What the campaign is worth if unwound now, plus cash already banked.

        Every open leg is priced at the side that would trade, so this is a
        number you could actually realize rather than a mid-based estimate of
        one. ``None`` when any open leg is missing that side of the market.
        """
        if self.anchor_mark is None or self.open_short_liability is None:
            return None
        return (
            self.anchor_mark
            - self.open_short_liability
            + self.realized_premium
            + self.open_short_credit
            - self.anchor_debit
        )

    @property
    def is_free_carry(self) -> bool:
        """True once banked premium fully covers what the anchor cost."""
        return self.anchor_debit > 0 and self.realized_premium >= self.anchor_debit


def build_ledgers(trades: list[Trade]) -> dict[tuple, ContractLedger]:
    grouped: dict[tuple, ContractLedger] = {}
    for t in trades:
        if not t.is_option:
            continue
        key = t.contract_key()
        if key not in grouped:
            grouped[key] = ContractLedger(
                symbol=t.symbol, kind=t.kind, strike=t.strike, expiry=t.expiry
            )
        grouped[key].trades.append(t)
    return grouped


def analyze_campaign(
    symbol: str,
    trades: list[Trade],
    asof: date,
    marks: dict[tuple, Quote] | None = None,
    anchor_key: tuple | None = None,
) -> CampaignMetrics:
    """Reduce a trade history to campaign metrics.

    ``marks`` maps a contract key to its live two-sided quote. Open legs are
    valued at the price that would close them -- the bid for the long anchor,
    the ask for a short weekly -- and a leg whose closing side is unquoted
    leaves the corresponding figure ``None`` rather than borrowing the other
    side of the book. ``anchor_key`` pins the long leg explicitly; when omitted
    the longest-dated net-long put is used, which is the anchor by definition.
    """
    marks = marks or {}
    relevant = [t for t in trades if t.symbol == symbol and t.kind == "put"]
    ledgers = build_ledgers(relevant)

    if anchor_key is None:
        longs = [l for l in ledgers.values() if l.net_quantity > 0]
        anchor = max(longs, key=lambda l: l.expiry) if longs else None
    else:
        anchor = ledgers.get(anchor_key)

    anchor_debit = 0.0
    anchor_mark: float | None = None
    if anchor is not None:
        # Cash flow on the anchor is negative (we paid); express as a positive debit.
        anchor_debit = max(-anchor.net_cash, 0.0)
        # Selling the anchor hits the bid, so that is what it is worth today.
        anchor_mark = close_value(
            marks.get((anchor.symbol, anchor.kind, anchor.strike, anchor.expiry)),
            anchor.net_quantity,
        )

    realized = 0.0
    open_credit = 0.0
    open_liability = 0.0
    have_all_marks = True
    settled = 0
    losers = 0
    assignments = 0
    commissions = 0.0
    first_short: date | None = None

    for key, ledger in ledgers.items():
        commissions += ledger.commissions
        if anchor is not None and key == (
            anchor.symbol,
            anchor.kind,
            anchor.strike,
            anchor.expiry,
        ):
            continue

        opened_short = any(t.quantity < 0 for t in ledger.trades)
        if not opened_short:
            continue

        first_short = ledger.opened_on if first_short is None else min(first_short, ledger.opened_on)
        if ledger.assigned:
            assignments += 1

        if ledger.is_settled(asof):
            realized += ledger.net_cash
            settled += 1
            if ledger.net_cash < 0:
                losers += 1
        else:
            open_credit += ledger.net_cash
            # Cost to flatten: buying the short back lifts the offer.
            cash = close_value(marks.get(key), ledger.net_quantity)
            if cash is None:
                have_all_marks = False
            else:
                # Buying back is cash out; the liability is that flipped positive.
                open_liability -= cash

    weeks = 0.0
    if first_short is not None:
        weeks = max((asof - first_short).days, 0) / 7.0

    return CampaignMetrics(
        symbol=symbol,
        asof=asof,
        anchor_strike=anchor.strike if anchor else None,
        anchor_expiry=anchor.expiry if anchor else None,
        anchor_quantity=anchor.net_quantity if anchor else 0.0,
        anchor_debit=anchor_debit,
        anchor_mark=anchor_mark,
        realized_premium=realized,
        open_short_credit=open_credit,
        open_short_liability=open_liability if have_all_marks else None,
        commissions=commissions,
        weeks_active=weeks,
        settled_short_legs=settled,
        losing_short_legs=losers,
        assignments=assignments,
    )


def weekly_history(symbol: str, trades: list[Trade], asof: date) -> list[dict]:
    """Per-short-leg results, oldest first, with a running recovery total.

    This is what feeds the campaign chart: each settled weekly sale as its own
    bar, plus the cumulative line showing progress against the anchor debit.
    """
    ledgers = build_ledgers([t for t in trades if t.symbol == symbol and t.kind == "put"])
    longs = [l for l in ledgers.values() if l.net_quantity > 0]
    anchor = max(longs, key=lambda l: l.expiry) if longs else None
    anchor_key = (
        (anchor.symbol, anchor.kind, anchor.strike, anchor.expiry) if anchor else None
    )

    rows = []
    running = 0.0
    shorts = [
        (k, l)
        for k, l in ledgers.items()
        if k != anchor_key and any(t.quantity < 0 for t in l.trades)
    ]
    for key, ledger in sorted(shorts, key=lambda kv: (kv[1].opened_on, kv[1].expiry)):
        settled = ledger.is_settled(asof)
        if settled:
            running += ledger.net_cash
        rows.append(
            {
                "strike": ledger.strike,
                "expiry": ledger.expiry.isoformat(),
                "opened": ledger.opened_on.isoformat(),
                "contracts": abs(min(ledger.net_quantity, 0.0)) or abs(ledger.trades[0].quantity),
                "net_cash": round(ledger.net_cash, 2),
                "settled": settled,
                "assigned": ledger.assigned,
                "cumulative": round(running, 2),
            }
        )
    return rows
