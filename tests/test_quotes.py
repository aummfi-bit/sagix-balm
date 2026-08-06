"""Side-aware pricing: buys lift the offer, sells hit the bid, and an
unquoted side reports nothing rather than borrowing the other side."""

from __future__ import annotations

from datetime import date

import pytest

from balm.quotes import Quote, close_value, closing_side, opening_side
from balm.snapshot import anchor_candidate_table, weekly_candidate_table
from balm.structure import Leg, evaluate_roll, net_greeks, price_leg

ASOF = date(2026, 8, 5)
SHORT_EXPIRY = date(2026, 8, 14)
ANCHOR_EXPIRY = date(2027, 1, 15)


def test_executable_takes_the_side_you_trade():
    q = Quote(bid=1.10, ask=1.30)
    assert q.executable("buy") == 1.30
    assert q.executable("sell") == 1.10
    assert q.mid == pytest.approx(1.20)


def test_an_empty_side_is_none_not_the_mid_or_the_last():
    no_bid = Quote(bid=0.0, ask=1.30, last=1.15, close=1.18)
    assert no_bid.executable("sell") is None
    assert no_bid.executable("buy") == 1.30
    assert not no_bid.two_sided

    nothing = Quote(last=1.15, close=1.18)
    assert nothing.executable("buy") is None
    assert nothing.executable("sell") is None
    # The reference price still falls back for display and for spot.
    assert nothing.mid == 1.15


def test_direction_follows_the_sign_of_the_position():
    q = Quote(bid=1.10, ask=1.30)
    assert opening_side(1) == "buy" and closing_side(1) == "sell"
    assert opening_side(-1) == "sell" and closing_side(-1) == "buy"
    assert q.to_open(1) == 1.30 and q.to_close(1) == 1.10
    assert q.to_open(-1) == 1.10 and q.to_close(-1) == 1.30


def test_close_value_signs_the_cash_flow():
    q = Quote(bid=1.10, ask=1.30)
    # Selling a long brings money in at the bid.
    assert close_value(q, 1) == pytest.approx(110.0)
    # Buying a short back pays out at the ask.
    assert close_value(q, -1) == pytest.approx(-130.0)
    assert close_value(None, -1) is None


def test_crossed_quotes_are_flagged():
    assert Quote(bid=1.40, ask=1.30).is_crossed
    assert not Quote(bid=1.10, ask=1.30).is_crossed


def test_leg_view_prices_both_directions():
    long_leg = Leg("put", 22.5, ANCHOR_EXPIRY, 1, 0.98, label="anchor")
    short_leg = Leg("put", 22.0, SHORT_EXPIRY, -1, 1.10, label="short")
    q = Quote(bid=1.10, ask=1.30)

    anchor = price_leg(long_leg, 22.70, ASOF, 0.04, 0.0, american=False, quote=q)
    short = price_leg(short_leg, 22.70, ASOF, 0.04, 0.0, american=False, quote=q)

    assert anchor.open_price == 1.30 and anchor.close_price == 1.10
    assert short.open_price == 1.10 and short.close_price == 1.30
    assert anchor.close_value == pytest.approx(110.0)
    assert short.close_value == pytest.approx(-130.0)


def test_liquidation_value_needs_every_leg_quoted():
    legs = [
        Leg("put", 22.5, ANCHOR_EXPIRY, 1, 0.98, label="anchor"),
        Leg("put", 22.0, SHORT_EXPIRY, -1, 1.10, label="short"),
    ]
    priced = net_greeks(
        legs, 22.70, ASOF, american=False, quotes=[Quote(bid=5.20, ask=5.80), Quote(bid=1.10, ask=1.30)]
    )
    # Sell the anchor at 5.20, buy the weekly back at 1.30.
    assert priced.liquidation_value == pytest.approx(520.0 - 130.0)
    # The model's own value is above that, by roughly the two half-spreads.
    assert priced.value > priced.liquidation_value

    half_quoted = net_greeks(
        legs, 22.70, ASOF, american=False, quotes=[Quote(bid=5.20, ask=5.80), None]
    )
    assert half_quoted.liquidation_value is None


def short_view(quote: Quote | None):
    leg = Leg("put", 22.0, SHORT_EXPIRY, -1, 1.10, label="short")
    return price_leg(leg, 22.70, ASOF, 0.04, 0.0, american=False, quote=quote)


def test_harvest_is_measured_against_the_ask():
    """Sold for 1.21; the ask is what actually buys it back."""
    view = short_view(Quote(bid=0.20, ask=0.30))
    checks = evaluate_roll(view, ASOF, sold_for=1.21, quote=Quote(bid=0.20, ask=0.30))
    harvest = next(c for c in checks if c.label == "Harvest target")

    # 1 - 0.30/1.21 = 75%, under the 80% target -- the mid would have said 79%
    # and a 0.20 bid would have said 83%.
    assert "75%" in harvest.detail
    assert "ask" in harvest.detail
    assert harvest.passed


def test_harvest_without_an_ask_is_unmeasurable_not_estimated():
    view = short_view(None)
    checks = evaluate_roll(view, ASOF, sold_for=1.21, quote=None)
    harvest = next(c for c in checks if c.label == "Harvest target")

    assert "cannot be measured" in harvest.detail
    # No percentage may be quoted off a model price.
    assert "%" not in harvest.detail
    assert not harvest.passed


def test_live_session_without_a_quote_fails_the_liquidity_check():
    checks = evaluate_roll(short_view(None), ASOF, sold_for=1.21, quote=None, live=True)
    liquidity = next(c for c in checks if c.label == "Liquidity")

    assert not liquidity.passed
    assert "No ask quoted" in liquidity.detail


def test_modeled_snapshot_makes_no_liquidity_claim():
    checks = evaluate_roll(short_view(None), ASOF, sold_for=1.21, quote=None, live=False)
    assert not any(c.label == "Liquidity" for c in checks)


def test_a_crossed_market_is_called_out():
    q = Quote(bid=1.40, ask=1.30)
    checks = evaluate_roll(short_view(q), ASOF, sold_for=1.21, quote=q, live=True)
    liquidity = next(c for c in checks if c.label == "Liquidity")

    assert not liquidity.passed
    assert "stale or locked" in liquidity.detail


def test_weekly_candidates_pay_the_bid_and_break_even_from_it():
    quotes = {(22.0, SHORT_EXPIRY): Quote(bid=1.05, ask=1.35)}
    rows = weekly_candidate_table(
        22.70,
        ASOF,
        SHORT_EXPIRY,
        [22.0, 22.5],
        1.10,
        0.04,
        0.0,
        anchor_theta_per_day=-0.01,
        quote_for=lambda k, e: quotes.get((k, e)),
    )
    quoted = next(r for r in rows if r["strike"] == 22.0)
    unquoted = next(r for r in rows if r["strike"] == 22.5)

    assert quoted["premium"] == pytest.approx(1.05)
    assert quoted["breakeven"] == pytest.approx(22.0 - 1.05)
    assert quoted["theoPremium"] != quoted["premium"]

    # No bid, no premium and no breakeven -- only the model's opinion, kept
    # under its own name.
    assert unquoted["premium"] is None
    assert unquoted["bid"] is None
    assert unquoted["breakeven"] is None
    assert unquoted["breakevenPct"] is None
    assert unquoted["premiumPctSpot"] is None
    assert unquoted["theoPremium"] > 0


def test_anchor_candidates_cost_the_ask_and_lengthen_the_payoff():
    quotes = {(22.5, ANCHOR_EXPIRY): Quote(bid=5.20, ask=5.80)}
    rows = anchor_candidate_table(
        22.70,
        ASOF,
        [(ANCHOR_EXPIRY, 0.98)],
        [22.5, 23.0],
        weekly_premium=1.05,
        rate=0.04,
        dividend_yield=0.0,
        quote_for=lambda k, e: quotes.get((k, e)),
    )
    quoted = next(r for r in rows if r["strike"] == 22.5)
    unquoted = next(r for r in rows if r["strike"] == 23.0)

    assert quoted["cost"] == pytest.approx(5.80)
    # Paying the offer and being paid the bid stretches the payoff horizon.
    assert quoted["weeksGross"] == pytest.approx(round(5.80 / 1.05, 1))

    assert unquoted["cost"] is None
    assert unquoted["costPctSpot"] is None
    assert unquoted["weeksGross"] is None
    assert unquoted["weeksRealistic"] is None
    assert unquoted["theoCost"] > 0


def test_payoff_horizon_needs_a_price_at_both_ends():
    """An anchor ask with no weekly bid cannot produce a week count."""
    quotes = {(22.5, ANCHOR_EXPIRY): Quote(bid=5.20, ask=5.80)}
    rows = anchor_candidate_table(
        22.70,
        ASOF,
        [(ANCHOR_EXPIRY, 0.98)],
        [22.5],
        weekly_premium=None,
        rate=0.04,
        dividend_yield=0.0,
        quote_for=lambda k, e: quotes.get((k, e)),
    )

    assert rows[0]["cost"] == pytest.approx(5.80)
    assert rows[0]["weeksGross"] is None
