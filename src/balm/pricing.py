"""Option pricing and greeks.

Two models are provided because the calendar strategy needs both:

* Black-Scholes for fast surface work (scenario grids, IV solving).
* Cox-Ross-Rubinstein binomial for American exercise, which is what actually
  matters for the short put leg. Equity options at IBKR are American, and a
  short put that goes deep in the money can be assigned before expiration.
  The gap between the two models is the early-exercise premium, and when that
  gap collapses toward zero the short leg is a live assignment candidate.

Rates and yields are continuously compounded. Time is in years. Volatility is
annualized as a decimal (0.90 == 90%).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Literal

OptionKind = Literal["put", "call"]

# Below this many years to expiry the closed-form greeks blow up, so we treat
# the option as expired and fall back to intrinsic value.
_MIN_T = 1e-9
_MIN_SIGMA = 1e-9


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def intrinsic(kind: OptionKind, spot: float, strike: float) -> float:
    return max(strike - spot, 0.0) if kind == "put" else max(spot - strike, 0.0)


@dataclass(frozen=True)
class Greeks:
    """Greeks in the units traders actually quote them in.

    ``theta`` is per calendar day, ``vega`` is per one volatility point
    (a 1% absolute move in IV), and ``rho`` is per one percentage point of rate.
    """

    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def scaled(self, quantity: float, multiplier: float = 100.0) -> "Greeks":
        """Scale to a position of ``quantity`` contracts.

        Quantity is signed: negative for a short leg. The multiplier is the
        contract size, 100 for standard US equity options.
        """
        f = quantity * multiplier
        return Greeks(
            price=self.price * f,
            delta=self.delta * f,
            gamma=self.gamma * f,
            theta=self.theta * f,
            vega=self.vega * f,
            rho=self.rho * f,
        )

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _d1_d2(
    spot: float, strike: float, t: float, rate: float, yield_: float, sigma: float
) -> tuple[float, float]:
    vol_t = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - yield_ + 0.5 * sigma * sigma) * t) / vol_t
    return d1, d1 - vol_t


def bs_price(
    kind: OptionKind,
    spot: float,
    strike: float,
    t: float,
    rate: float = 0.0,
    yield_: float = 0.0,
    sigma: float = 0.0,
) -> float:
    if t <= _MIN_T or sigma <= _MIN_SIGMA:
        return intrinsic(kind, spot, strike)
    d1, d2 = _d1_d2(spot, strike, t, rate, yield_, sigma)
    disc_r = math.exp(-rate * t)
    disc_q = math.exp(-yield_ * t)
    if kind == "call":
        return spot * disc_q * norm_cdf(d1) - strike * disc_r * norm_cdf(d2)
    return strike * disc_r * norm_cdf(-d2) - spot * disc_q * norm_cdf(-d1)


def bs_greeks(
    kind: OptionKind,
    spot: float,
    strike: float,
    t: float,
    rate: float = 0.0,
    yield_: float = 0.0,
    sigma: float = 0.0,
) -> Greeks:
    if t <= _MIN_T or sigma <= _MIN_SIGMA:
        val = intrinsic(kind, spot, strike)
        if kind == "put":
            delta = -1.0 if spot < strike else 0.0
        else:
            delta = 1.0 if spot > strike else 0.0
        return Greeks(price=val, delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = _d1_d2(spot, strike, t, rate, yield_, sigma)
    disc_r = math.exp(-rate * t)
    disc_q = math.exp(-yield_ * t)
    sqrt_t = math.sqrt(t)
    pdf_d1 = norm_pdf(d1)

    gamma = disc_q * pdf_d1 / (spot * sigma * sqrt_t)
    vega = spot * disc_q * pdf_d1 * sqrt_t
    decay = -spot * disc_q * pdf_d1 * sigma / (2.0 * sqrt_t)

    if kind == "call":
        price = spot * disc_q * norm_cdf(d1) - strike * disc_r * norm_cdf(d2)
        delta = disc_q * norm_cdf(d1)
        theta = decay - rate * strike * disc_r * norm_cdf(d2) + yield_ * spot * disc_q * norm_cdf(d1)
        rho = strike * t * disc_r * norm_cdf(d2)
    else:
        price = strike * disc_r * norm_cdf(-d2) - spot * disc_q * norm_cdf(-d1)
        delta = -disc_q * norm_cdf(-d1)
        theta = decay + rate * strike * disc_r * norm_cdf(-d2) - yield_ * spot * disc_q * norm_cdf(-d1)
        rho = -strike * t * disc_r * norm_cdf(-d2)

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta=theta / 365.0,
        vega=vega / 100.0,
        rho=rho / 100.0,
    )


def implied_vol(
    kind: OptionKind,
    price: float,
    spot: float,
    strike: float,
    t: float,
    rate: float = 0.0,
    yield_: float = 0.0,
    lo: float = 1e-4,
    hi: float = 8.0,
    tol: float = 1e-8,
) -> float | None:
    """Back out Black-Scholes IV by bisection.

    Bisection rather than Newton: GLXY-class names quote IVs north of 300% on
    expiration-day contracts, where Newton's vega denominator is small enough
    to send iterates negative. Monotonicity of price in sigma makes bisection
    unconditionally safe, and the cost is negligible at this scale.

    Returns ``None`` when the price is outside the no-arbitrage band the model
    can reach, which usually means a stale or crossed quote.
    """
    if t <= _MIN_T or price <= 0:
        return None
    if price < intrinsic(kind, spot, strike) - 1e-9:
        return None
    if bs_price(kind, spot, strike, t, rate, yield_, hi) < price:
        return None

    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if bs_price(kind, spot, strike, t, rate, yield_, mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def american_price(
    kind: OptionKind,
    spot: float,
    strike: float,
    t: float,
    rate: float = 0.0,
    yield_: float = 0.0,
    sigma: float = 0.0,
    steps: int = 240,
) -> float:
    """Cox-Ross-Rubinstein binomial price with early exercise at every node."""
    if t <= _MIN_T or sigma <= _MIN_SIGMA:
        return intrinsic(kind, spot, strike)

    dt = t / steps
    up = math.exp(sigma * math.sqrt(dt))
    down = 1.0 / up
    disc = math.exp(-rate * dt)
    p = (math.exp((rate - yield_) * dt) - down) / (up - down)
    p = min(max(p, 0.0), 1.0)

    # Terminal payoffs, index j = number of up moves.
    values = [
        intrinsic(kind, spot * (up**j) * (down ** (steps - j)), strike)
        for j in range(steps + 1)
    ]

    for step in range(steps - 1, -1, -1):
        for j in range(step + 1):
            hold = disc * (p * values[j + 1] + (1.0 - p) * values[j])
            node_spot = spot * (up**j) * (down ** (step - j))
            values[j] = max(hold, intrinsic(kind, node_spot, strike))

    return values[0]


def american_greeks(
    kind: OptionKind,
    spot: float,
    strike: float,
    t: float,
    rate: float = 0.0,
    yield_: float = 0.0,
    sigma: float = 0.0,
    steps: int = 240,
) -> Greeks:
    """American greeks by central finite differences around the binomial price.

    Bumps are chosen large enough to stay above binomial lattice noise: the
    CRR tree is only piecewise smooth, so very small bumps produce garbage
    second derivatives.
    """
    if t <= _MIN_T or sigma <= _MIN_SIGMA:
        return bs_greeks(kind, spot, strike, t, rate, yield_, sigma)

    def price_at(s: float, vol: float, tt: float, r: float) -> float:
        return american_price(kind, s, strike, tt, r, yield_, vol, steps)

    ds = max(spot * 0.01, 0.01)
    dv = 0.01
    base = price_at(spot, sigma, t, rate)
    up_s = price_at(spot + ds, sigma, t, rate)
    down_s = price_at(spot - ds, sigma, t, rate)

    delta = (up_s - down_s) / (2.0 * ds)
    gamma = (up_s - 2.0 * base + down_s) / (ds * ds)
    vega = (price_at(spot, sigma + dv, t, rate) - price_at(spot, sigma - dv, t, rate)) / (2.0 * dv)

    day = 1.0 / 365.0
    if t > day:
        theta = price_at(spot, sigma, t - day, rate) - base
    else:
        theta = intrinsic(kind, spot, strike) - base

    dr = 0.0001
    rho = (price_at(spot, sigma, t, rate + dr) - price_at(spot, sigma, t, rate - dr)) / (2.0 * dr)

    return Greeks(
        price=base,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega / 100.0,
        rho=rho / 100.0,
    )


def early_exercise_premium(
    kind: OptionKind,
    spot: float,
    strike: float,
    t: float,
    rate: float = 0.0,
    yield_: float = 0.0,
    sigma: float = 0.0,
    steps: int = 240,
) -> float:
    """American value in excess of European value.

    For a short put this is the cushion against assignment. As it approaches
    zero the holder gives up nothing by exercising early, so assignment
    becomes rational rather than merely possible.
    """
    amer = american_price(kind, spot, strike, t, rate, yield_, sigma, steps)
    euro = bs_price(kind, spot, strike, t, rate, yield_, sigma)
    return max(amer - euro, 0.0)


def extrinsic(kind: OptionKind, price: float, spot: float, strike: float) -> float:
    return max(price - intrinsic(kind, spot, strike), 0.0)
