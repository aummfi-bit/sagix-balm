"""Assemble the JSON snapshot the canvas consumes.

Two modes produce the same schema:

* **live** -- TWS marks and greeks plus Flex history, for a position you hold.
* **model** -- pure Black-Scholes from a spot and volatility you supply, for
  designing a structure before any of it exists.

Keeping one schema means the canvas does not care which produced it, and a
model snapshot can be diffed against a live one to see how far reality has
drifted from the plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .campaign import CampaignMetrics, Trade, analyze_campaign, weekly_history
from .config import Config, Underlying
from .pricing import bs_greeks, bs_price
from .structure import (
    Leg,
    LegView,
    evaluate_roll,
    net_greeks,
    price_leg,
    scenario_grid,
    suggest_short_strike,
    year_fraction,
)

SCHEMA_VERSION = 2
DEFAULT_SPOT_MOVES = [-0.40, -0.30, -0.20, -0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40]


@dataclass
class StructureSpec:
    """The calendar being modeled for one underlying."""

    anchor_strike: float
    anchor_expiry: date
    anchor_iv: float
    short_strike: float
    short_expiry: date
    short_iv: float
    contracts: int = 1
    anchor_debit: float | None = None  # actual fill, if known
    short_credit: float | None = None  # actual fill, if known
    # Leverage of 30-day IV to spot. Low-volatility ETFs react far less
    # violently than single-name crypto proxies, so this is per-underlying.
    vol_beta: float = 1.5

    def legs(self) -> list[Leg]:
        return [
            Leg("put", self.anchor_strike, self.anchor_expiry, self.contracts, self.anchor_iv, label="anchor"),
            Leg("put", self.short_strike, self.short_expiry, -self.contracts, self.short_iv, label="short"),
        ]


def _greeks_dict(view: LegView) -> dict[str, Any]:
    return {
        "label": view.leg.label,
        "strike": view.leg.strike,
        "expiry": view.leg.expiry.isoformat(),
        "dte": view.dte,
        "quantity": view.leg.quantity,
        "iv": round(view.leg.iv, 4),
        "price": round(view.unit_price, 4),
        "intrinsic": round(view.unit_intrinsic, 4),
        "extrinsic": round(view.unit_extrinsic, 4),
        "earlyExercise": round(view.early_exercise, 4),
        "delta": round(view.per_contract.delta, 4),
        "gamma": round(view.per_contract.gamma, 5),
        "theta": round(view.per_contract.theta, 4),
        "vega": round(view.per_contract.vega, 4),
        "positionTheta": round(view.position.theta, 2),
        "positionVega": round(view.position.vega, 2),
        "positionDelta": round(view.position.delta, 2),
        "positionValue": round(view.position.price, 2),
    }


def anchor_candidate_table(
    spot: float,
    asof: date,
    expiries: list[tuple[date, float]],
    strikes: list[float],
    weekly_premium: float,
    rate: float,
    dividend_yield: float,
    capture_rate: float = 0.75,
) -> list[dict[str, Any]]:
    """Cost and payoff horizon for each candidate anchor.

    ``weeksToCover`` is the honest figure: the gross count divided by the
    share of theoretical premium actually captured after early closes,
    losing weeks and commissions.
    """
    rows = []
    for expiry, iv in expiries:
        t = year_fraction(expiry, asof)
        for k in strikes:
            g = bs_greeks("put", spot, k, t, rate, dividend_yield, iv)
            gross = g.price / weekly_premium if weekly_premium > 0 else None
            rows.append(
                {
                    "expiry": expiry.isoformat(),
                    "dte": (expiry - asof).days,
                    "strike": k,
                    "iv": round(iv, 4),
                    "cost": round(g.price, 2),
                    "costPctSpot": round(g.price / spot, 4),
                    "delta": round(g.delta, 4),
                    "theta": round(g.theta, 4),
                    "vega": round(g.vega, 4),
                    "weeksGross": round(gross, 1) if gross else None,
                    "weeksRealistic": round(gross / capture_rate, 1) if gross else None,
                }
            )
    return rows


def weekly_candidate_table(
    spot: float,
    asof: date,
    expiry: date,
    strikes: list[float],
    iv: float,
    rate: float,
    dividend_yield: float,
    anchor_theta_per_day: float,
) -> list[dict[str, Any]]:
    """Each sellable weekly strike, with the greeks that drive the roll choice."""
    t = year_fraction(expiry, asof)
    rows = []
    for k in strikes:
        g = bs_greeks("put", spot, k, t, rate, dividend_yield, iv)
        rows.append(
            {
                "strike": k,
                "expiry": expiry.isoformat(),
                "dte": (expiry - asof).days,
                "premium": round(g.price, 2),
                "premiumPctSpot": round(g.price / spot, 4),
                "delta": round(g.delta, 4),
                "gamma": round(g.gamma, 5),
                "theta": round(g.theta, 4),
                "vega": round(g.vega, 4),
                # How many times over the weekly's decay covers the anchor's bleed.
                "thetaCoverage": round(abs(g.theta / anchor_theta_per_day), 1)
                if anchor_theta_per_day
                else None,
                "breakeven": round(k - g.price, 2),
                "breakevenPct": round((k - g.price) / spot - 1.0, 4),
            }
        )
    return rows


def _term_structure(spec: StructureSpec) -> dict[str, Any]:
    """Whether the volatility term structure pays you to run this calendar.

    A put calendar is short the front tenor and long the back one, so it wants
    backwardation: front implied volatility above long-dated implied
    volatility means you sell the expensive tenor and buy the cheap one. Under
    contango the trade is fighting the surface, and every roll sells vol for
    less than the anchor cost you.
    """
    edge = spec.short_iv - spec.anchor_iv
    if edge > 0.02:
        shape = "backwardation"
        verdict = "Front volatility is richer than the anchor's. The surface pays you to run this calendar."
    elif edge < -0.02:
        shape = "contango"
        verdict = "Front volatility is cheaper than the anchor's. Every roll sells vol for less than the anchor cost, so the surface works against this calendar."
    else:
        shape = "flat"
        verdict = "Front and long-dated volatility are close. No structural edge either way; the trade lives or dies on theta and strike selection."
    return {
        "shortIv": round(spec.short_iv, 4),
        "anchorIv": round(spec.anchor_iv, 4),
        "edge": round(edge, 4),
        "shape": shape,
        "verdict": verdict,
    }


def _campaign_dict(m: CampaignMetrics) -> dict[str, Any]:
    return {
        "anchorStrike": m.anchor_strike,
        "anchorExpiry": m.anchor_expiry.isoformat() if m.anchor_expiry else None,
        "anchorQuantity": m.anchor_quantity,
        "anchorDebit": round(m.anchor_debit, 2),
        "anchorMark": round(m.anchor_mark, 2) if m.anchor_mark is not None else None,
        "realizedPremium": round(m.realized_premium, 2),
        "openShortCredit": round(m.open_short_credit, 2),
        "openShortLiability": round(m.open_short_liability, 2)
        if m.open_short_liability is not None
        else None,
        "commissions": round(m.commissions, 2),
        "recoveryPct": round(m.recovery_pct, 4) if m.recovery_pct is not None else None,
        "remainingDebit": round(m.remaining_debit, 2),
        "weeksActive": round(m.weeks_active, 1),
        "settledShortLegs": m.settled_short_legs,
        "losingShortLegs": m.losing_short_legs,
        "assignments": m.assignments,
        "avgWeeklyCapture": round(m.avg_weekly_capture, 2)
        if m.avg_weekly_capture is not None
        else None,
        "weeksToPayoff": round(m.weeks_to_payoff, 1) if m.weeks_to_payoff is not None else None,
        "netLiquidation": round(m.net_liquidation, 2) if m.net_liquidation is not None else None,
        "isFreeCarry": m.is_free_carry,
    }


def build_underlying_block(
    u: Underlying,
    spec: StructureSpec,
    spot: float,
    asof: date,
    config: Config,
    *,
    anchor_expiries: list[tuple[date, float]],
    anchor_strikes: list[float],
    weekly_strikes: list[float],
    trades: list[Trade] | None = None,
    marks: dict[tuple, float] | None = None,
    quote_bid: float | None = None,
    quote_ask: float | None = None,
    source: str = "model",
) -> dict[str, Any]:
    rate = config.model.risk_free_rate
    q = config.model.dividend_yield
    legs = spec.legs()

    ng = net_greeks(legs, spot, asof, rate, q, american=True, steps=config.model.binomial_steps)
    anchor_view, short_view = ng.legs[0], ng.legs[1]

    atm_weekly = bs_price("put", spot, spot, year_fraction(spec.short_expiry, asof), rate, q, spec.short_iv)

    scenarios_by_horizon = {}
    for horizon in (1, 3, 7):
        scenarios_by_horizon[str(horizon)] = [
            {
                "spot": round(p.spot, 2),
                "movePct": p.spot_move_pct,
                "ivShock": round(p.iv_shock_30d, 4),
                "pnl": round(p.pnl, 2),
            }
            for p in scenario_grid(
                legs, spot, asof, horizon, DEFAULT_SPOT_MOVES, rate, q, vol_beta=spec.vol_beta
            )
        ]

    checks = evaluate_roll(
        short_view,
        asof,
        sold_for=spec.short_credit,
        bid=quote_bid,
        ask=quote_ask,
        roll_by_weekday=config.rules.roll_by_weekday,
        extrinsic_harvest_target=config.rules.extrinsic_harvest_target,
        short_delta_max=config.rules.short_delta_max,
        assignment_warn_premium=config.rules.assignment_warn_premium,
        max_spread_pct=config.rules.max_spread_pct,
    )

    next_strike = suggest_short_strike(
        spot,
        spec.short_expiry + timedelta(days=7),
        asof,
        spec.short_iv,
        weekly_strikes,
        rate,
        q,
        config.rules.short_delta_target,
    )

    block: dict[str, Any] = {
        "symbol": u.symbol,
        "label": u.display(),
        "source": source,
        "spot": round(spot, 4),
        "structure": {
            "contracts": spec.contracts,
            "anchorDebit": spec.anchor_debit,
            "shortCredit": spec.short_credit,
            "legs": [_greeks_dict(anchor_view), _greeks_dict(short_view)],
            "net": {
                "delta": round(ng.delta, 2),
                "gamma": round(ng.gamma, 4),
                "theta": round(ng.theta, 2),
                "vega": round(ng.vega, 2),
                "value": round(ng.value, 2),
                "shortTheta": round(ng.short_theta, 2),
                "longTheta": round(ng.long_theta, 2),
                "thetaRatio": round(ng.theta_ratio, 1) if ng.theta_ratio else None,
            },
        },
        "atmWeeklyPremium": round(atm_weekly, 2),
        "termStructure": _term_structure(spec),
        "volBeta": spec.vol_beta,
        "anchorCandidates": anchor_candidate_table(
            spot, asof, anchor_expiries, anchor_strikes, atm_weekly, rate, q
        ),
        "weeklyCandidates": weekly_candidate_table(
            spot,
            asof,
            spec.short_expiry,
            weekly_strikes,
            spec.short_iv,
            rate,
            q,
            anchor_view.per_contract.theta,
        ),
        "scenarios": scenarios_by_horizon,
        "rollChecks": [
            {"passed": c.passed, "label": c.label, "detail": c.detail} for c in checks
        ],
        "suggestedNextStrike": next_strike,
    }

    if trades:
        metrics = analyze_campaign(u.symbol, trades, asof, marks or {})
        block["campaign"] = _campaign_dict(metrics)
        block["weeklyHistory"] = weekly_history(u.symbol, trades, asof)

    return block


def write_snapshot(payload: dict[str, Any], directory: Path, name: str = "latest") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))

    stamped = directory / f"{payload['asof']}-{name}.json"
    stamped.write_text(json.dumps(payload, indent=2, default=str))
    return path


def new_payload(asof: date, account: dict[str, float] | None, config: Config) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asof": asof.isoformat(),
        "account": account or {},
        "rules": {
            "rollByWeekday": config.rules.roll_by_weekday,
            "extrinsicHarvestTarget": config.rules.extrinsic_harvest_target,
            "shortDeltaTarget": config.rules.short_delta_target,
            "shortDeltaMax": config.rules.short_delta_max,
            "assignmentWarnPremium": config.rules.assignment_warn_premium,
            "maxSpreadPct": config.rules.max_spread_pct,
        },
        "model": {
            "riskFreeRate": config.model.risk_free_rate,
            "dividendYield": config.model.dividend_yield,
        },
        "underlyings": [],
    }
