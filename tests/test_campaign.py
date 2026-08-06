"""Campaign accounting against a hand-built trade history."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from balm.campaign import Trade, analyze_campaign, weekly_history
from balm.quotes import Quote

SYM = "GLXY"
ANCHOR_EXPIRY = date(2027, 1, 15)


def anchor_buy() -> Trade:
    # Bought 1 Jan-27 22.5 put for $5.49, $1.05 commission.
    return Trade(
        symbol=SYM,
        trade_date=date(2026, 7, 1),
        quantity=1,
        price=5.49,
        commission=-1.05,
        kind="put",
        strike=22.5,
        expiry=ANCHOR_EXPIRY,
        trade_id="anchor-1",
    )


def sell(strike: float, expiry: date, price: float, on: date, tid: str) -> Trade:
    return Trade(
        symbol=SYM,
        trade_date=on,
        quantity=-1,
        price=price,
        commission=-0.65,
        kind="put",
        strike=strike,
        expiry=expiry,
        trade_id=tid,
    )


def buy_back(strike: float, expiry: date, price: float, on: date, tid: str) -> Trade:
    return Trade(
        symbol=SYM,
        trade_date=on,
        quantity=1,
        price=price,
        commission=-0.65,
        kind="put",
        strike=strike,
        expiry=expiry,
        trade_id=tid,
    )


def test_cash_flow_signs():
    assert sell(22.0, date(2026, 7, 10), 1.20, date(2026, 7, 6), "s").cash_flow == pytest.approx(
        119.35
    )
    assert anchor_buy().cash_flow == pytest.approx(-550.05)


def test_anchor_debit_includes_commission():
    m = analyze_campaign(SYM, [anchor_buy()], date(2026, 7, 2))
    assert m.anchor_debit == pytest.approx(550.05)
    assert m.realized_premium == 0.0
    assert m.recovery_pct == 0.0
    assert not m.is_free_carry


def test_three_closed_weeks_accumulate_recovery():
    """Sell, buy back at 20% of the credit, three weeks running."""
    trades = [anchor_buy()]
    weeks = [
        (date(2026, 7, 6), date(2026, 7, 10), 22.0, 1.20, 0.24),
        (date(2026, 7, 13), date(2026, 7, 17), 22.0, 1.30, 0.26),
        (date(2026, 7, 20), date(2026, 7, 24), 22.5, 1.40, 0.28),
    ]
    for i, (opened, expiry, strike, credit, close_at) in enumerate(weeks):
        trades.append(sell(strike, expiry, credit, opened, f"s{i}"))
        trades.append(buy_back(strike, expiry, close_at, expiry, f"b{i}"))

    m = analyze_campaign(SYM, trades, date(2026, 7, 27))

    # Each week nets (credit - buyback) * 100 minus two commissions.
    expected = sum((c - b) * 100 - 1.30 for _, _, _, c, b in weeks)
    assert m.realized_premium == pytest.approx(expected)
    assert m.settled_short_legs == 3
    assert m.losing_short_legs == 0
    assert m.recovery_pct == pytest.approx(expected / 550.05)
    # Roughly half the anchor recovered after three weeks, not all of it.
    assert 0.4 < m.recovery_pct < 0.65


def test_losing_week_reduces_recovery_and_is_counted():
    trades = [
        anchor_buy(),
        sell(22.0, date(2026, 7, 10), 1.20, date(2026, 7, 6), "s0"),
        buy_back(22.0, date(2026, 7, 10), 3.40, date(2026, 7, 9), "b0"),
    ]
    m = analyze_campaign(SYM, trades, date(2026, 7, 12))
    assert m.realized_premium < 0
    assert m.losing_short_legs == 1
    assert m.recovery_pct < 0


def test_expired_short_counts_as_settled_without_a_closing_trade():
    """A worthless expiry produces no closing fill, so settlement is inferred
    from the expiration date having passed."""
    trades = [anchor_buy(), sell(20.0, date(2026, 7, 10), 0.55, date(2026, 7, 6), "s0")]
    m = analyze_campaign(SYM, trades, date(2026, 7, 20))
    assert m.settled_short_legs == 1
    assert m.realized_premium == pytest.approx(0.55 * 100 - 0.65)


def test_open_short_is_not_counted_as_realized():
    trades = [anchor_buy(), sell(22.0, date(2026, 8, 14), 1.21, date(2026, 8, 3), "s0")]
    marks = {(SYM, "put", 22.0, date(2026, 8, 14)): Quote(bid=0.85, ask=0.95)}
    m = analyze_campaign(SYM, trades, date(2026, 8, 5), marks)

    assert m.realized_premium == 0.0
    assert m.open_short_credit == pytest.approx(1.21 * 100 - 0.65)
    # Closing the short means paying the offer: $95, not the $90 mid.
    assert m.open_short_liability == pytest.approx(95.0)


def test_anchor_is_valued_at_the_bid_and_the_short_at_the_ask():
    """The whole point: each leg is priced at the side that would trade."""
    trades = [anchor_buy(), sell(22.0, date(2026, 8, 14), 1.21, date(2026, 8, 3), "s0")]
    marks = {
        (SYM, "put", 22.5, ANCHOR_EXPIRY): Quote(bid=5.20, ask=5.80),
        (SYM, "put", 22.0, date(2026, 8, 14)): Quote(bid=0.85, ask=0.95),
    }
    m = analyze_campaign(SYM, trades, date(2026, 8, 5), marks)

    # Selling the anchor hits the bid: $520, not the $550 mid.
    assert m.anchor_mark == pytest.approx(520.0)
    assert m.open_short_liability == pytest.approx(95.0)
    # Both spreads are paid, so liquidation is below the mid-based figure.
    assert m.net_liquidation == pytest.approx(
        520.0 - 95.0 + 0.0 + (1.21 * 100 - 0.65) - 550.05
    )


def test_a_missing_quote_makes_the_valuation_unknown_not_optimistic():
    trades = [anchor_buy(), sell(22.0, date(2026, 8, 14), 1.21, date(2026, 8, 3), "s0")]
    # The anchor is quoted, the open short is not.
    marks = {(SYM, "put", 22.5, ANCHOR_EXPIRY): Quote(bid=5.20, ask=5.80)}
    m = analyze_campaign(SYM, trades, date(2026, 8, 5), marks)

    assert m.anchor_mark == pytest.approx(520.0)
    assert m.open_short_liability is None
    # An unpriceable short must not be silently treated as costing nothing.
    assert m.net_liquidation is None


def test_a_bidless_anchor_has_no_mark():
    """Nobody bidding is an absence of a price, not a price of zero."""
    marks = {(SYM, "put", 22.5, ANCHOR_EXPIRY): Quote(bid=0.0, ask=5.80)}
    m = analyze_campaign(SYM, [anchor_buy()], date(2026, 8, 5), marks)

    assert m.anchor_mark is None
    assert m.net_liquidation is None


def test_assignment_is_flagged():
    trades = [
        anchor_buy(),
        sell(22.0, date(2026, 7, 10), 1.20, date(2026, 7, 6), "s0"),
        Trade(
            symbol=SYM,
            trade_date=date(2026, 7, 9),
            quantity=1,
            price=0.0,
            kind="put",
            strike=22.0,
            expiry=date(2026, 7, 10),
            notes="A",
            trade_id="assign-1",
        ),
    ]
    m = analyze_campaign(SYM, trades, date(2026, 7, 12))
    assert m.assignments == 1


def test_free_carry_once_premium_covers_the_anchor():
    trades = [anchor_buy()]
    for i in range(8):
        expiry = date(2026, 7, 10) + timedelta(weeks=i)
        trades.append(sell(22.0, expiry, 1.20, expiry - timedelta(days=4), f"s{i}"))
    m = analyze_campaign(SYM, trades, date(2026, 10, 1))

    assert m.realized_premium > m.anchor_debit
    assert m.is_free_carry
    assert m.recovery_pct > 1.0
    assert m.remaining_debit == 0.0


def test_weeks_to_payoff_extrapolates_the_achieved_rate():
    trades = [anchor_buy()]
    for i in range(2):
        expiry = date(2026, 7, 10) + timedelta(weeks=i)
        trades.append(sell(22.0, expiry, 1.20, expiry - timedelta(days=4), f"s{i}"))
    m = analyze_campaign(SYM, trades, date(2026, 7, 20))

    assert m.avg_weekly_capture > 0
    # Two weeks in at ~$119/wk against a $550 anchor: several more to go.
    assert m.weeks_to_payoff > 2


def test_weekly_history_runs_a_cumulative_total():
    trades = [anchor_buy()]
    for i in range(3):
        expiry = date(2026, 7, 10) + timedelta(weeks=i)
        trades.append(sell(22.0, expiry, 1.20, expiry - timedelta(days=4), f"s{i}"))
    rows = weekly_history(SYM, trades, date(2026, 8, 1))

    assert len(rows) == 3
    assert [r["settled"] for r in rows] == [True, True, True]
    assert rows[-1]["cumulative"] == pytest.approx(3 * (1.20 * 100 - 0.65))
    # The anchor must never appear as an income leg.
    assert all(r["strike"] == 22.0 for r in rows)
