"""Assemble the JSON snapshot the canvas consumes.

Two modes produce the same schema:

* **live** -- TWS marks and greeks plus Flex history, for a position you hold.
* **model** -- pure Black-Scholes from a spot and volatility you supply, for
  designing a structure before any of it exists.

Keeping one schema means the canvas does not care which produced it, and a
model snapshot can be diffed against a live one to see how far reality has
drifted from the plan.

Every price that stands for a transaction is emitted on the side that would
actually trade -- ``ask`` where the row describes buying, ``bid`` where it
describes selling -- and is ``null`` when the market does not quote that side.
Nothing is substituted for a missing quote: anything derived from one (a
breakeven, a payoff horizon, a cost as a share of spot) goes ``null`` with it.
The model's own value travels alongside under a ``theo`` name so the two can
never be mistaken for each other, and a model snapshot reports no transactable
price at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .campaign import CampaignMetrics, Trade, analyze_campaign, weekly_history
from .config import Config, Underlying
from .pricing import bs_greeks, bs_price
from .quotes import Quote
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

SCHEMA_VERSION = 3
DEFAULT_SPOT_MOVES = [-0.40, -0.30, -0.20, -0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40]

# (strike, expiry) -> the live market in that put, when there is one.
QuoteLookup = Callable[[float, date], "Quote | None"]


def _no_quotes(strike: float, expiry: date) -> "Quote | None":
    return None


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


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
    q = view.quote
    return {
        "label": view.leg.label,
        "strike": view.leg.strike,
        "expiry": view.leg.expiry.isoformat(),
        "dte": view.dte,
        "quantity": view.leg.quantity,
        "iv": round(view.leg.iv, 4),
        # Theoretical value, not a fill.
        "price": round(view.unit_price, 4),
        "bid": q.bid if q else None,
        "ask": q.ask if q else None,
        "spreadPct": _round(q.spread_pct, 4) if q else None,
        # What putting the leg on and taking it off actually price at.
        "openPrice": view.open_price,
        "closePrice": view.close_price,
        "closeValue": _round(view.close_value, 2),
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
    weekly_premium: float | None,
    rate: float,
    dividend_yield: float,
    capture_rate: float = 0.75,
    quote_for: QuoteLookup = _no_quotes,
) -> list[dict[str, Any]]:
    """Cost and payoff horizon for each candidate anchor.

    Buying the anchor means lifting the offer, so ``cost`` is the ask, and it
    is ``null`` where none is quoted -- an anchor nobody will sell you does not
    have a price just because the model has an opinion about one.

    ``weeksToCover`` is the honest figure: the gross count divided by the share
    of premium actually captured after early closes, losing weeks and
    commissions. It needs a real price at both ends -- the ask you pay for the
    anchor and the bid the weekly pays you -- so it is ``null`` without them.
    """
    rows = []
    for expiry, iv in expiries:
        t = year_fraction(expiry, asof)
        for k in strikes:
            g = bs_greeks("put", spot, k, t, rate, dividend_yield, iv)
            quote = quote_for(k, expiry)
            cost = quote.executable("buy") if quote else None
            gross = (
                cost / weekly_premium
                if cost is not None and weekly_premium is not None and weekly_premium > 0
                else None
            )
            rows.append(
                {
                    "expiry": expiry.isoformat(),
                    "dte": (expiry - asof).days,
                    "strike": k,
                    "iv": round(iv, 4),
                    # The model's value, never to be read as a fill.
                    "theoCost": round(g.price, 2),
                    "bid": quote.bid if quote else None,
                    "ask": cost,
                    # What buying it actually costs.
                    "cost": cost,
                    "costPctSpot": _round(cost / spot if cost is not None else None, 4),
                    "delta": round(g.delta, 4),
                    "theta": round(g.theta, 4),
                    "vega": round(g.vega, 4),
                    "weeksGross": _round(gross, 1),
                    "weeksRealistic": _round(gross / capture_rate if gross else None, 1),
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
    quote_for: QuoteLookup = _no_quotes,
) -> list[dict[str, Any]]:
    """Each sellable weekly strike, with the greeks that drive the roll choice.

    These rows describe a sale, so ``premium`` is the bid -- what a seller is
    actually paid -- and the breakeven is struck from it. Where nobody is
    bidding there is no premium and no breakeven, only the model's opinion in
    ``theoPremium``, which buys you nothing.
    """
    t = year_fraction(expiry, asof)
    rows = []
    for k in strikes:
        g = bs_greeks("put", spot, k, t, rate, dividend_yield, iv)
        quote = quote_for(k, expiry)
        premium = quote.executable("sell") if quote else None
        breakeven = k - premium if premium is not None else None
        rows.append(
            {
                "strike": k,
                "expiry": expiry.isoformat(),
                "dte": (expiry - asof).days,
                # The model's value, never to be read as a fill.
                "theoPremium": round(g.price, 2),
                "bid": premium,
                "ask": quote.ask if quote else None,
                # What selling it actually pays.
                "premium": premium,
                "premiumPctSpot": _round(
                    premium / spot if premium is not None else None, 4
                ),
                "delta": round(g.delta, 4),
                "gamma": round(g.gamma, 5),
                "theta": round(g.theta, 4),
                "vega": round(g.vega, 4),
                # How many times over the weekly's decay covers the anchor's bleed.
                "thetaCoverage": round(abs(g.theta / anchor_theta_per_day), 1)
                if anchor_theta_per_day
                else None,
                "breakeven": _round(breakeven, 2),
                "breakevenPct": _round(
                    breakeven / spot - 1.0 if breakeven is not None else None, 4
                ),
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
    marks: dict[tuple, Quote] | None = None,
    source: str = "model",
) -> dict[str, Any]:
    rate = config.model.risk_free_rate
    q = config.model.dividend_yield
    legs = spec.legs()
    live = source == "live"

    quotes = marks or {}

    def quote_for(strike: float, expiry: date) -> Quote | None:
        return quotes.get((u.symbol, "put", strike, expiry))

    anchor_quote = quote_for(spec.anchor_strike, spec.anchor_expiry)
    short_quote = quote_for(spec.short_strike, spec.short_expiry)

    ng = net_greeks(
        legs,
        spot,
        asof,
        rate,
        q,
        american=True,
        steps=config.model.binomial_steps,
        quotes=[anchor_quote, short_quote],
    )
    anchor_view, short_view = ng.legs[0], ng.legs[1]

    # Price the weekly benchmark off a strike that actually exists, since that
    # is the one a bid can be quoted on.
    atm_strike = (
        min(weekly_strikes, key=lambda k: abs(k - spot)) if weekly_strikes else spot
    )
    atm_theo = bs_price(
        "put", spot, atm_strike, year_fraction(spec.short_expiry, asof), rate, q, spec.short_iv
    )
    atm_quote = quote_for(atm_strike, spec.short_expiry)
    # What one week of selling actually pays. Null without a bid, and every
    # payoff horizon derived from it goes null too.
    atm_weekly = atm_quote.executable("sell") if atm_quote else None

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
        quote=short_quote,
        live=live,
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
                # The model's value of the structure...
                "value": round(ng.value, 2),
                # ...and what unwinding it at the quotes would actually pay.
                "liquidationValue": _round(ng.liquidation_value, 2),
                "shortTheta": round(ng.short_theta, 2),
                "longTheta": round(ng.long_theta, 2),
                "thetaRatio": round(ng.theta_ratio, 1) if ng.theta_ratio else None,
            },
        },
        "atmWeeklyPremium": atm_weekly,
        "atmWeeklyStrike": atm_strike,
        "atmWeeklyTheo": round(atm_theo, 2),
        "termStructure": _term_structure(spec),
        "volBeta": spec.vol_beta,
        "anchorCandidates": anchor_candidate_table(
            spot, asof, anchor_expiries, anchor_strikes, atm_weekly, rate, q,
            quote_for=quote_for,
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
            quote_for=quote_for,
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
