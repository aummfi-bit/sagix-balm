"""Command line entry point.

    balm doctor            check the config and that the quote feed answers
    balm plan              model-only snapshot from the [underlyings.plan] blocks
    balm quotes            snapshot priced off Cboe's delayed feed

Both snapshot commands write the same schema, so a plan can be diffed against
what the market actually quotes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta

from .config import Config, Underlying
from .snapshot import StructureSpec, build_underlying_block, new_payload, write_snapshot

log = logging.getLogger("balm")


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _next_friday(asof: date) -> date:
    ahead = (4 - asof.weekday()) % 7
    return asof + timedelta(days=ahead or 7)


def _strike_ladder(spot: float, window: float, step: float | None = None) -> list[float]:
    """Strikes around spot on a realistic listing increment."""
    if step is None:
        step = 0.5 if spot < 25 else (1.0 if spot < 100 else 2.5)
    lo, hi = spot * (1 - window), spot * (1 + window)
    first = round(lo / step) * step
    out, k = [], first
    while k <= hi + 1e-9:
        if k > 0:
            out.append(round(k, 2))
        k += step
    return out


def _spec_from_plan(u: Underlying, asof: date) -> tuple[StructureSpec, float]:
    plan = u.plan or {}
    missing = [k for k in ("spot", "anchor_strike", "anchor_expiry") if k not in plan]
    if missing:
        raise SystemExit(
            f"[underlyings.plan] for {u.symbol} is missing: {', '.join(missing)}"
        )

    spot = float(plan["spot"])
    short_expiry = (
        _as_date(plan["short_expiry"]) if "short_expiry" in plan else _next_friday(asof)
    )
    return (
        StructureSpec(
            anchor_strike=float(plan["anchor_strike"]),
            anchor_expiry=_as_date(plan["anchor_expiry"]),
            anchor_iv=float(plan.get("anchor_iv", 0.60)),
            short_strike=float(plan.get("short_strike", spot)),
            short_expiry=short_expiry,
            short_iv=float(plan.get("short_iv", plan.get("anchor_iv", 0.60))),
            contracts=int(plan.get("contracts", 1)),
            anchor_debit=plan.get("anchor_debit"),
            short_credit=plan.get("short_credit"),
            vol_beta=float(plan.get("vol_beta", 1.5)),
        ),
        spot,
    )


def _anchor_expiry_candidates(u: Underlying, asof: date, spec: StructureSpec) -> list[tuple[date, float]]:
    """Long-dated expiries to compare, from config or defaulting to the plan's own."""
    raw = (u.plan or {}).get("anchor_expiry_candidates")
    if not raw:
        return [(spec.anchor_expiry, spec.anchor_iv)]
    return [(_as_date(item["expiry"]), float(item["iv"])) for item in raw]


def cmd_plan(config: Config, args: argparse.Namespace) -> int:
    asof = _as_date(args.asof) if args.asof else date.today()
    payload = new_payload(asof, None, config)

    for u in config.underlyings:
        spec, spot = _spec_from_plan(u, asof)
        window = config.model.strike_window
        block = build_underlying_block(
            u,
            spec,
            spot,
            asof,
            config,
            anchor_expiries=_anchor_expiry_candidates(u, asof, spec),
            anchor_strikes=_strike_ladder(spot, window),
            weekly_strikes=_strike_ladder(spot, window),
            source="model",
        )
        payload["underlyings"].append(block)

    path = write_snapshot(payload, config.snapshot_dir, name=args.name)
    print(f"Wrote model snapshot for {', '.join(config.symbols())} to {path}")
    return 0


def _spec_from_chain(u: Underlying, chain, asof: date) -> StructureSpec:
    """The planned structure, repriced against the strikes Cboe actually lists.

    The plan block says which legs to model; everything numeric about them --
    spot, implied volatility -- comes from the feed, because a plan written
    weeks ago carries a spot that has since moved.
    """
    spec, _plan_spot = _spec_from_plan(u, asof)
    index = chain.marks_index

    def iv_for(strike: float, expiry: date, fallback: float) -> float:
        q = index.get((chain.symbol, "put", strike, expiry))
        return q.iv if q and q.iv else fallback

    for label, strike, expiry in (
        ("anchor", spec.anchor_strike, spec.anchor_expiry),
        ("short", spec.short_strike, spec.short_expiry),
    ):
        if (chain.symbol, "put", strike, expiry) not in index:
            log.warning(
                "%s %s leg %.2fP %s is not listed in the Cboe chain; its prices "
                "will be reported as unquoted.",
                u.symbol,
                label,
                strike,
                expiry,
            )

    spec.anchor_iv = iv_for(spec.anchor_strike, spec.anchor_expiry, spec.anchor_iv)
    spec.short_iv = iv_for(spec.short_strike, spec.short_expiry, spec.short_iv)
    return spec


def cmd_quotes(config: Config, args: argparse.Namespace) -> int:
    """Snapshot built from Cboe's delayed feed: no TWS, no account, no secrets.

    This is the one path that runs anywhere -- a laptop, a cron job, a cloud
    runner -- because it authenticates against nothing. It gives real prices
    for the chain and nothing at all about the account.
    """
    from .cboe import NOMINAL_DELAY_MINUTES, CboeError, fetch_chain

    asof = _as_date(args.asof) if args.asof else date.today()
    payload = new_payload(asof, None, config)
    payload["quoteSource"] = "cboe"
    payload["quotesDelayed"] = True
    payload["quoteDelayMinutes"] = NOMINAL_DELAY_MINUTES

    failures = 0
    stamps: list[str] = []
    for u in config.underlyings:
        try:
            chain = fetch_chain(u.symbol, config.model.quote_timeout)
        except CboeError as exc:
            failures += 1
            log.error("%s: %s", u.symbol, exc)
            continue

        if chain.as_of:
            stamps.append(chain.as_of.isoformat(sep=" "))

        spec = _spec_from_chain(u, chain, asof)
        strikes = chain.strikes(config.model.strike_window)
        block = build_underlying_block(
            u,
            spec,
            chain.spot,
            asof,
            config,
            anchor_expiries=_anchor_expiry_candidates(u, asof, spec),
            anchor_strikes=strikes,
            weekly_strikes=strikes,
            marks=chain.marks(),
            source="cboe",
        )
        # Publish only the near-the-money slice. The full chain runs to
        # thousands of contracts, nearly all of them strikes the desk will
        # never show, and the snapshot is a file that gets committed.
        window = set(strikes)
        block["chain"] = [q.as_dict() for q in chain.quotes if q.strike in window]
        block["quote"] = chain.underlying.as_dict()
        block["iv30"] = chain.iv30
        block["quotesAsOf"] = chain.as_of.isoformat(sep=" ") if chain.as_of else None
        payload["underlyings"].append(block)

    if not payload["underlyings"]:
        print("No chains could be fetched from Cboe.", file=sys.stderr)
        return 1

    payload["quotesAsOf"] = max(stamps) if stamps else None
    path = write_snapshot(payload, config.snapshot_dir, name=args.name)
    print(
        f"Wrote delayed-quote snapshot to {path} "
        f"(Cboe, as of {payload['quotesAsOf'] or 'unknown'} ET, ~{NOMINAL_DELAY_MINUTES} min behind)"
    )
    return 1 if failures else 0


def cmd_doctor(config: Config, args: argparse.Namespace) -> int:
    ok = True
    print(f"Underlyings   : {', '.join(config.symbols())}")

    for u in config.underlyings:
        state = "configured" if u.plan else "no [underlyings.plan] block (plan mode unavailable)"
        print(f"  {u.symbol:<6} {state}")

    # The only external dependency left is a public feed, so the only thing
    # worth checking is whether it answers and how stale what it returns is.
    from .cboe import NOMINAL_DELAY_MINUTES, CboeError, fetch_chain

    for u in config.underlyings:
        try:
            chain = fetch_chain(u.symbol, config.model.quote_timeout)
        except CboeError as exc:
            ok = False
            print(f"Cboe {u.symbol:<9}: UNREACHABLE ({exc})")
            continue
        stamp = chain.as_of.isoformat(sep=" ") if chain.as_of else "no timestamp"
        print(
            f"Cboe {u.symbol:<9}: {len(chain.quotes)} contracts, "
            f"spot {chain.spot:.2f}, quoted {stamp} ET"
        )

    print(f"Quote delay   : ~{NOMINAL_DELAY_MINUTES} min — indicative, not executable")
    print(f"Snapshot dir  : {config.snapshot_dir.resolve()}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="balm", description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="check configuration and connectivity")
    p_doctor.set_defaults(func=cmd_doctor)

    p_plan = sub.add_parser("plan", help="model-only snapshot, no market data")
    p_plan.add_argument("--asof", help="YYYY-MM-DD, defaults to today")
    p_plan.add_argument("--name", default="latest")
    p_plan.set_defaults(func=cmd_plan)

    p_quotes = sub.add_parser(
        "quotes", help="snapshot priced off Cboe's delayed feed"
    )
    p_quotes.add_argument("--asof", help="YYYY-MM-DD, defaults to today")
    p_quotes.add_argument("--name", default="latest")
    p_quotes.set_defaults(func=cmd_quotes)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        config = Config.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return args.func(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
