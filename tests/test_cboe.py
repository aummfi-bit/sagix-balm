"""Parsing Cboe's delayed feed.

The payload shape below is trimmed from a real response, keeping one contract
of each awkward kind: a normal two-sided market, a call, a fractional strike,
and a row where the feed could not compute an implied volatility.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from balm.cboe import CboeError, parse_chain, parse_osi

PAYLOAD = {
    "timestamp": "2026-08-06 15:49:05",
    "symbol": "GLXY",
    "data": {
        "symbol": "GLXY",
        "current_price": 20.145,
        "bid": 20.12,
        "ask": 20.17,
        "prev_day_close": 19.07,
        "iv30": 94.832,
        "options": [
            {
                "option": "GLXY260814P00022000",
                "bid": 2.25,
                "ask": 2.43,
                "iv": 0.9629,
                "open_interest": 148.0,
                "delta": -0.6994,
                "gamma": 0.1049,
                "vega": 0.0154,
                "theta": -0.0635,
                "last_trade_price": 3.1,
                "prev_day_close": 2.98,
            },
            {
                "option": "GLXY270115C00022500",
                "bid": 4.2,
                "ask": 4.7,
                "iv": 0.9884,
                "open_interest": 997.0,
                "delta": 0.5642,
                "gamma": 0.0435,
                "vega": 0.0559,
                "theta": -0.015,
                "last_trade_price": 4.4,
                "prev_day_close": 4.35,
            },
            {
                "option": "GLXY260807C00001000",
                "bid": 17.1,
                "ask": 20.4,
                # The feed writes 0 when it cannot compute one.
                "iv": 0.0,
                "open_interest": 0.0,
                "delta": 1.0,
                "gamma": 0.0,
                "vega": 0.0,
                "theta": 0.0,
                "last_trade_price": 0.0,
                "prev_day_close": 0.0,
            },
            {
                "option": "GLXY260814P00022500",
                "bid": 0.0,
                "ask": 3.1,
                "iv": 0.98,
                "open_interest": 5.0,
                "delta": -0.75,
                "gamma": 0.1,
                "vega": 0.01,
                "theta": -0.05,
                "last_trade_price": 2.9,
                "prev_day_close": 2.9,
            },
        ],
    },
}


def test_parse_osi_splits_root_expiry_right_and_strike():
    assert parse_osi("GLXY260814P00022000") == ("GLXY", date(2026, 8, 14), "put", 22.0)
    assert parse_osi("GLXY270115C00022500") == ("GLXY", date(2027, 1, 15), "call", 22.5)
    # Variable-width roots are parsed from the right.
    assert parse_osi("A260814P00007500") == ("A", date(2026, 8, 14), "put", 7.5)
    # Thousandths, so fractional strikes survive.
    assert parse_osi("IBIT260814P00035500")[3] == 35.5


def test_parse_osi_rejects_non_options():
    with pytest.raises(ValueError):
        parse_osi("GLXY")
    with pytest.raises(ValueError):
        parse_osi("GLXY260814X00022000")


def test_chain_carries_both_sides_and_the_underlying():
    chain = parse_chain(PAYLOAD)

    assert chain.symbol == "GLXY"
    assert chain.spot == pytest.approx(20.145)
    assert chain.underlying.bid == 20.12 and chain.underlying.ask == 20.17
    assert chain.as_of == datetime(2026, 8, 6, 15, 49, 5)
    # Published as a percentage, stored as a decimal like every other vol here.
    assert chain.iv30 == pytest.approx(0.94832)

    index = chain.marks_index
    short = index[("GLXY", "put", 22.0, date(2026, 8, 14))]
    assert short.quote.bid == 2.25 and short.quote.ask == 2.43
    assert short.quote.executable("sell") == 2.25
    assert short.quote.executable("buy") == 2.43
    assert short.iv == pytest.approx(0.9629)
    assert short.open_interest == 148.0


def test_a_zero_iv_is_absent_not_zero():
    chain = parse_chain(PAYLOAD)
    deep = chain.marks_index[("GLXY", "call", 1.0, date(2026, 8, 7))]
    assert deep.iv is None


def test_a_zero_bid_is_not_a_sellable_price():
    """Nobody bidding means the sell side has no price at all."""
    chain = parse_chain(PAYLOAD)
    bidless = chain.marks_index[("GLXY", "put", 22.5, date(2026, 8, 14))]

    assert bidless.quote.bid == 0.0
    assert bidless.quote.executable("sell") is None
    assert bidless.quote.executable("buy") == 3.1
    assert not bidless.quote.two_sided


def test_strikes_can_be_windowed_around_spot():
    chain = parse_chain(PAYLOAD)
    assert chain.strikes() == [1.0, 22.0, 22.5]
    # The far out-of-the-money leftover drops out of a 25% window.
    assert chain.strikes(0.25) == [22.0, 22.5]


def test_marks_are_keyed_for_the_campaign_ledger():
    chain = parse_chain(PAYLOAD)
    marks = chain.marks()
    assert ("GLXY", "put", 22.0, date(2026, 8, 14)) in marks
    assert ("GLXY", "call", 22.5, date(2027, 1, 15)) in marks


def test_a_payload_without_a_price_is_an_error():
    broken = {"timestamp": "2026-08-06 15:49:05", "data": {"symbol": "X", "options": []}}
    with pytest.raises(CboeError):
        parse_chain(broken)


def test_a_payload_missing_its_data_block_is_an_error():
    with pytest.raises(CboeError):
        parse_chain({"timestamp": "2026-08-06 15:49:05"})
