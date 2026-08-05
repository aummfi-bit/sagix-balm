"""Pricing checks against textbook Black-Scholes values and model invariants."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from balm.pricing import (
    american_price,
    bs_greeks,
    bs_price,
    early_exercise_premium,
    implied_vol,
)
from balm.structure import Leg, net_greeks, scenario_grid, term_iv_shift

# Hull's standard worked example: S=100, K=100, T=1, r=5%, sigma=20%.
S, K, T, R, SIG = 100.0, 100.0, 1.0, 0.05, 0.20


def test_black_scholes_price_matches_reference():
    assert bs_price("call", S, K, T, R, 0.0, SIG) == pytest.approx(10.4506, abs=1e-4)
    assert bs_price("put", S, K, T, R, 0.0, SIG) == pytest.approx(5.5735, abs=1e-4)


def test_put_call_parity():
    call = bs_price("call", S, K, T, R, 0.0, SIG)
    put = bs_price("put", S, K, T, R, 0.0, SIG)
    assert call - put == pytest.approx(S - K * math.exp(-R * T), abs=1e-9)


def test_greeks_match_reference():
    call = bs_greeks("call", S, K, T, R, 0.0, SIG)
    put = bs_greeks("put", S, K, T, R, 0.0, SIG)

    assert call.delta == pytest.approx(0.63683, abs=1e-4)
    assert put.delta == pytest.approx(-0.36317, abs=1e-4)
    assert call.gamma == pytest.approx(0.018762, abs=1e-5)
    # Vega is quoted per volatility point, theta per calendar day.
    assert call.vega == pytest.approx(37.524 / 100.0, abs=1e-4)
    assert call.theta == pytest.approx(-6.414 / 365.0, abs=1e-4)
    assert call.rho == pytest.approx(53.2325 / 100.0, abs=1e-3)


def test_greeks_agree_with_finite_differences():
    g = bs_greeks("put", S, K, T, R, 0.0, SIG)
    h = 1e-4
    fd_delta = (
        bs_price("put", S + h, K, T, R, 0.0, SIG) - bs_price("put", S - h, K, T, R, 0.0, SIG)
    ) / (2 * h)
    assert g.delta == pytest.approx(fd_delta, abs=1e-6)


def test_implied_vol_round_trips():
    for sigma in (0.15, 0.55, 1.20, 3.00):
        price = bs_price("put", S, K, 0.05, R, 0.0, sigma)
        assert implied_vol("put", price, S, K, 0.05, R) == pytest.approx(sigma, abs=1e-5)


def test_implied_vol_rejects_impossible_price():
    # Below intrinsic is unreachable by any volatility.
    assert implied_vol("put", 1.0, 50.0, 100.0, 0.5, R) is None


def test_american_put_is_worth_at_least_european():
    for spot in (60.0, 90.0, 100.0, 140.0):
        amer = american_price("put", spot, K, T, R, 0.0, SIG, steps=300)
        euro = bs_price("put", spot, K, T, R, 0.0, SIG)
        assert amer >= euro - 1e-6


def test_american_call_without_dividend_equals_european():
    # Never optimal to exercise a call early with no dividend, so the binomial
    # should converge to the closed form.
    amer = american_price("call", S, K, T, R, 0.0, SIG, steps=600)
    assert amer == pytest.approx(bs_price("call", S, K, T, R, 0.0, SIG), abs=0.02)


def test_early_exercise_premium_collapses_when_deep_in_the_money():
    """A deep ITM short put loses its early-exercise cushion, which is exactly
    the condition that makes assignment rational for the holder."""
    near = early_exercise_premium("put", 100.0, 100.0, 0.02, R, 0.0, 0.9)
    deep = early_exercise_premium("put", 40.0, 100.0, 0.02, R, 0.0, 0.9)
    assert deep > near


def test_term_iv_shift_damps_with_tenor():
    """A 20-point shock to 30-day IV must reach long-dated vol far weaker."""
    assert term_iv_shift(20.0, 30) == pytest.approx(20.0, abs=1e-9)
    assert term_iv_shift(20.0, 365) == pytest.approx(20.0 * math.sqrt(30 / 365), abs=1e-6)
    # Amplification on very short tenors is capped.
    assert term_iv_shift(20.0, 1) <= 20.0 * 4.0 + 1e-9


def test_atm_calendar_on_high_vol_name_is_net_long_delta():
    """The counterintuitive result the tool exists to surface.

    At ~100% IV the long-dated ATM put carries a much smaller absolute delta
    than the weekly ATM put, so an ATM anchor against an ATM weekly is net
    long delta and loses on the first move down.
    """
    today = date(2026, 8, 5)
    legs = [
        Leg("put", 22.5, today + timedelta(days=534), 1, 0.90, label="anchor"),
        Leg("put", 22.5, today + timedelta(days=7), -1, 1.10, label="weekly"),
    ]
    ng = net_greeks(legs, 22.70, today, rate=0.04, american=False)

    anchor, weekly = ng.legs[0], ng.legs[1]
    assert abs(anchor.per_contract.delta) < abs(weekly.per_contract.delta)
    assert ng.delta > 0


def test_calendar_theta_is_positive_and_short_leg_dominates():
    today = date(2026, 8, 5)
    legs = [
        Leg("put", 22.5, today + timedelta(days=534), 1, 0.90),
        Leg("put", 22.5, today + timedelta(days=7), -1, 1.10),
    ]
    ng = net_greeks(legs, 22.70, today, rate=0.04, american=False)
    assert ng.short_theta > 0
    assert ng.long_theta < 0
    assert ng.theta > 0
    assert ng.theta_ratio > 1.0


def test_crash_scenario_hurts_less_than_naive_parallel_shift_suggests():
    """Vol term structure matters: the short leg's IV rises much more."""
    today = date(2026, 8, 5)
    legs = [
        Leg("put", 22.5, today + timedelta(days=534), 1, 0.90),
        Leg("put", 22.5, today + timedelta(days=7), -1, 1.10),
    ]
    points = scenario_grid(legs, 22.70, today, 7, [-0.30, 0.0, 0.30], vol_beta=1.5)
    crash, flat, rally = points

    assert crash.iv_shock_30d > 0  # crash inflates short-dated vol
    assert rally.iv_shock_30d < 0  # rally crushes it
    assert flat.pnl > 0  # unchanged spot: theta is harvested
