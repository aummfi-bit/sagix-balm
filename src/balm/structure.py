"""Analytics for a put calendar / diagonal.

The structure is one long-dated put (the "anchor") against one or more
short-dated puts (the "income" legs). Two effects drive nearly everything the
trader needs to see, and both are easy to get wrong by intuition:

1. **Delta is not what you expect.** On a very high implied volatility name,
   a long-dated at-the-money put has a much smaller absolute delta than a
   weekly at-the-money put, because the ``sigma^2/2 * T`` drift term in ``d1``
   pushes the long-dated option far out of the money in forward terms. An ATM
   anchor against an ATM weekly can therefore be net *long* delta, meaning the
   position loses on the first leg down rather than gaining. ``net_greeks``
   reports this explicitly.

2. **Volatility shocks are not parallel.** Short-dated implied volatility
   moves far more than long-dated in a crash. ``term_iv_shift`` applies the
   square-root-of-time damping that equity surfaces empirically follow, so a
   crash scenario correctly hurts the short leg more than it helps the anchor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .pricing import (
    Greeks,
    OptionKind,
    american_greeks,
    american_price,
    bs_greeks,
    bs_price,
    early_exercise_premium,
    extrinsic,
    intrinsic,
)

DAYS_PER_YEAR = 365.0
# Reference tenor for a quoted volatility shock, in days.
_VOL_SHOCK_REFERENCE_DTE = 30.0
# A 1-day option's IV can move violently but not without limit; cap the
# term-structure amplifier so scenarios stay in a believable range.
_MAX_VOL_AMPLIFIER = 4.0


def year_fraction(expiry: date, asof: date) -> float:
    return max((expiry - asof).days, 0) / DAYS_PER_YEAR


def term_iv_shift(shock_30d: float, dte: float, power: float = 0.5) -> float:
    """Scale a 30-day IV shock to another tenor.

    Implied volatility term structure moves are damped roughly as the inverse
    square root of time: a shock that lifts 30-day IV by 20 points lifts
    1-year IV by about 20 * sqrt(30/365) ~= 5.7 points. This is why a crash
    inflates the short put you are short far more than the anchor you own.
    """
    amplifier = math.sqrt(_VOL_SHOCK_REFERENCE_DTE / max(dte, 1.0)) ** (power / 0.5)
    return shock_30d * min(amplifier, _MAX_VOL_AMPLIFIER)


@dataclass
class Leg:
    """One option leg. ``quantity`` is signed: negative means short."""

    kind: OptionKind
    strike: float
    expiry: date
    quantity: float
    iv: float
    multiplier: float = 100.0
    label: str = ""

    def dte(self, asof: date) -> int:
        return max((self.expiry - asof).days, 0)

    def is_short(self) -> bool:
        return self.quantity < 0


@dataclass
class LegView:
    """A leg priced at a point in time, with position-scaled greeks."""

    leg: Leg
    dte: int
    unit_price: float
    unit_intrinsic: float
    unit_extrinsic: float
    early_exercise: float
    per_contract: Greeks
    position: Greeks


@dataclass
class NetGreeks:
    legs: list[LegView]
    delta: float
    gamma: float
    theta: float
    vega: float
    value: float
    # Theta earned on short legs versus theta bled by long legs. The engine of
    # the strategy is that this stays comfortably positive.
    short_theta: float
    long_theta: float

    @property
    def theta_ratio(self) -> float | None:
        """How many times the anchor's daily bleed the short legs cover."""
        if self.long_theta == 0:
            return None
        return abs(self.short_theta / self.long_theta)


def price_leg(
    leg: Leg,
    spot: float,
    asof: date,
    rate: float,
    dividend_yield: float,
    american: bool = True,
    steps: int = 240,
) -> LegView:
    t = year_fraction(leg.expiry, asof)
    if american:
        greeks = american_greeks(leg.kind, spot, leg.strike, t, rate, dividend_yield, leg.iv, steps)
    else:
        greeks = bs_greeks(leg.kind, spot, leg.strike, t, rate, dividend_yield, leg.iv)

    return LegView(
        leg=leg,
        dte=leg.dte(asof),
        unit_price=greeks.price,
        unit_intrinsic=intrinsic(leg.kind, spot, leg.strike),
        unit_extrinsic=extrinsic(leg.kind, greeks.price, spot, leg.strike),
        early_exercise=early_exercise_premium(
            leg.kind, spot, leg.strike, t, rate, dividend_yield, leg.iv, steps
        )
        if american
        else 0.0,
        per_contract=greeks,
        position=greeks.scaled(leg.quantity, leg.multiplier),
    )


def net_greeks(
    legs: list[Leg],
    spot: float,
    asof: date,
    rate: float = 0.04,
    dividend_yield: float = 0.0,
    american: bool = True,
    steps: int = 240,
) -> NetGreeks:
    views = [price_leg(l, spot, asof, rate, dividend_yield, american, steps) for l in legs]
    short_theta = sum(v.position.theta for v in views if v.leg.is_short())
    long_theta = sum(v.position.theta for v in views if not v.leg.is_short())
    return NetGreeks(
        legs=views,
        delta=sum(v.position.delta for v in views),
        gamma=sum(v.position.gamma for v in views),
        theta=sum(v.position.theta for v in views),
        vega=sum(v.position.vega for v in views),
        value=sum(v.position.price for v in views),
        short_theta=short_theta,
        long_theta=long_theta,
    )


def position_value(
    legs: list[Leg],
    spot: float,
    asof: date,
    rate: float,
    dividend_yield: float,
    iv_shock_30d: float = 0.0,
    american: bool = False,
    steps: int = 120,
) -> float:
    """Mark the whole structure, optionally under a term-damped IV shock.

    Defaults to the European model because this is called thousands of times
    when building a scenario grid; the binomial is reserved for point-in-time
    greeks and assignment checks where the difference matters.
    """
    total = 0.0
    for leg in legs:
        t = year_fraction(leg.expiry, asof)
        dte = max((leg.expiry - asof).days, 0)
        iv = max(leg.iv + term_iv_shift(iv_shock_30d, dte), 0.01)
        if american:
            unit = american_price(leg.kind, spot, leg.strike, t, rate, dividend_yield, iv, steps)
        else:
            unit = bs_price(leg.kind, spot, leg.strike, t, rate, dividend_yield, iv)
        total += unit * leg.quantity * leg.multiplier
    return total


@dataclass
class ScenarioPoint:
    spot: float
    spot_move_pct: float
    iv_shock_30d: float
    value: float
    pnl: float


def scenario_grid(
    legs: list[Leg],
    spot: float,
    asof: date,
    horizon_days: int,
    spot_moves: list[float],
    rate: float = 0.04,
    dividend_yield: float = 0.0,
    vol_beta: float = 1.5,
    american: bool = False,
) -> list[ScenarioPoint]:
    """Value the structure ``horizon_days`` forward across a range of spot moves.

    ``vol_beta`` is the leverage of 30-day implied volatility to spot: a value
    of 1.5 means a 10% drop in the underlying lifts 30-day IV by 15 points.
    Crypto-linked equities exhibit a strong negative spot/vol correlation, so
    the shock is applied asymmetrically -- downside moves inflate volatility,
    upside moves deflate it at a damped rate, matching the observed skew.
    """
    base = position_value(legs, spot, asof, rate, dividend_yield, 0.0, american)
    future = asof + timedelta(days=horizon_days)
    points: list[ScenarioPoint] = []

    for move in spot_moves:
        new_spot = spot * (1.0 + move)
        # Rallies crush volatility, but less violently than crashes inflate it.
        shock = -vol_beta * move if move < 0 else -vol_beta * move * 0.5
        value = position_value(legs, new_spot, future, rate, dividend_yield, shock, american)
        points.append(
            ScenarioPoint(
                spot=new_spot,
                spot_move_pct=move,
                iv_shock_30d=shock,
                value=value,
                pnl=value - base,
            )
        )
    return points


@dataclass
class RollCheck:
    passed: bool
    label: str
    detail: str


def evaluate_roll(
    short_view: LegView,
    asof: date,
    *,
    sold_for: float | None,
    bid: float | None = None,
    ask: float | None = None,
    roll_by_weekday: int = 2,
    extrinsic_harvest_target: float = 0.80,
    short_delta_max: float = 0.55,
    assignment_warn_premium: float = 0.10,
    max_spread_pct: float = 0.10,
) -> list[RollCheck]:
    """Run the weekly management rules against the current short leg.

    Each check is phrased so that ``passed`` means "no action required".
    """
    checks: list[RollCheck] = []
    leg = short_view.leg

    # Gamma in the final days is the dominant risk; the rule is to be out
    # before it peaks rather than to manage through it.
    if short_view.dte <= 2:
        checks.append(
            RollCheck(
                False,
                "Expiration gamma",
                f"{short_view.dte} DTE. Gamma peaks now; close or roll immediately.",
            )
        )
    elif asof.weekday() > roll_by_weekday and short_view.dte <= 4:
        checks.append(
            RollCheck(
                False,
                "Roll deadline",
                f"Past the weekday-{roll_by_weekday} cutoff with {short_view.dte} DTE remaining.",
            )
        )
    else:
        checks.append(
            RollCheck(True, "Roll deadline", f"{short_view.dte} DTE, inside the roll window.")
        )

    if sold_for is not None and sold_for > 0:
        harvested = 1.0 - (short_view.unit_price / sold_for)
        if harvested >= extrinsic_harvest_target:
            checks.append(
                RollCheck(
                    False,
                    "Harvest target",
                    f"{harvested:.0%} of the credit captured (target {extrinsic_harvest_target:.0%}). Take it off.",
                )
            )
        else:
            checks.append(
                RollCheck(
                    True,
                    "Harvest target",
                    f"{harvested:.0%} of the credit captured, below the {extrinsic_harvest_target:.0%} target.",
                )
            )

    abs_delta = abs(short_view.per_contract.delta)
    if abs_delta > short_delta_max:
        checks.append(
            RollCheck(
                False,
                "Strike drift",
                f"Short delta {abs_delta:.2f} exceeds {short_delta_max:.2f}. Roll down to restore a near-ATM strike.",
            )
        )
    else:
        checks.append(RollCheck(True, "Strike drift", f"Short delta {abs_delta:.2f}."))

    if short_view.unit_intrinsic <= 0:
        checks.append(
            RollCheck(
                True,
                "Assignment risk",
                f"Strike is ${short_view.leg.strike:.2f} against a higher spot, so the put is out of the money and will not be assigned.",
            )
        )
    elif short_view.early_exercise < assignment_warn_premium:
        checks.append(
            RollCheck(
                False,
                "Assignment risk",
                f"In the money with only ${short_view.early_exercise:.2f} of early-exercise value left. The holder gives up almost nothing by exercising now.",
            )
        )
    else:
        checks.append(
            RollCheck(
                True,
                "Assignment risk",
                f"In the money but ${short_view.early_exercise:.2f} of early-exercise value still makes holding better than exercising.",
            )
        )

    if bid is not None and ask is not None and ask > 0:
        mid = 0.5 * (bid + ask)
        spread_pct = (ask - bid) / mid if mid > 0 else 1.0
        if spread_pct > max_spread_pct:
            checks.append(
                RollCheck(
                    False,
                    "Liquidity",
                    f"Spread is {spread_pct:.0%} of mid (${bid:.2f}/${ask:.2f}). Work a mid-price limit, never market.",
                )
            )
        else:
            checks.append(RollCheck(True, "Liquidity", f"Spread {spread_pct:.0%} of mid."))

    return checks


def suggest_short_strike(
    spot: float,
    expiry: date,
    asof: date,
    iv: float,
    strikes: list[float],
    rate: float = 0.04,
    dividend_yield: float = 0.0,
    target_delta: float = 0.40,
) -> float | None:
    """Pick the listed strike whose put delta sits closest to the target.

    Selecting by delta rather than by a fixed distance from spot is what keeps
    the short leg in the same risk bucket week to week as both the underlying
    and its volatility move around.
    """
    if not strikes:
        return None
    t = year_fraction(expiry, asof)
    best, best_err = None, float("inf")
    for k in strikes:
        d = abs(bs_greeks("put", spot, k, t, rate, dividend_yield, iv).delta)
        err = abs(d - target_delta)
        if err < best_err:
            best, best_err = k, err
    return best
