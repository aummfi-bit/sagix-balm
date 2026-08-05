"""Command line entry point.

    balm doctor            check config, TWS reachability and Flex credentials
    balm plan              model-only snapshot from the [underlyings.plan] blocks
    balm sync              live snapshot: TWS marks and greeks + Flex history
    balm flex --out FILE   download the raw Flex statement for inspection
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .campaign import Trade
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


def cmd_sync(config: Config, args: argparse.Namespace) -> int:
    from .flex import FlexError, download_trades, merge_trades
    from .tws import TwsClient

    asof = date.today()
    symbols = set(config.symbols())

    flex_trades: list[Trade] = []
    if args.flex_file:
        from .flex import load_trades_file

        flex_trades = load_trades_file(args.flex_file, symbols)
        log.info("Loaded %d trades from %s", len(flex_trades), args.flex_file)
    elif config.flex.query_id and config.flex.token():
        try:
            flex_trades = download_trades(config.flex, symbols)
            log.info("Downloaded %d trades from Flex", len(flex_trades))
        except FlexError as exc:
            # A missing history degrades the campaign panel but leaves every
            # live greek and scenario intact, so this is a warning not a stop.
            log.warning("Flex download failed, continuing without history: %s", exc)
    else:
        log.warning("Flex not configured; campaign history will be unavailable.")

    with TwsClient(config) as client:
        account = client.account_values()
        payload = new_payload(asof, account, config)
        today_trades = client.todays_trades(symbols)
        trades = merge_trades(flex_trades, today_trades)

        for u in config.underlyings:
            stock = client.stock_contract(u)
            spot_quote = client.spot(stock)
            spot = spot_quote.mid
            if not spot:
                log.error("No usable price for %s; skipping.", u.symbol)
                continue

            chain = client.option_chain(
                u, stock, spot, config.model.expirations, config.model.strike_window
            )
            positions = client.positions([u.symbol])

            marks = {
                (q.symbol, q.kind, q.strike, q.expiry): q.quote.mid
                for q in chain
                if q.quote.mid is not None
            }

            spec, plan_spot = _resolve_live_spec(u, positions, chain, spot, asof)
            if spec is None:
                log.warning("No put calendar found in %s positions; using plan block.", u.symbol)
                spec, plan_spot = _spec_from_plan(u, asof)

            short_quote = next(
                (
                    q
                    for q in chain
                    if q.strike == spec.short_strike and q.expiry == spec.short_expiry
                ),
                None,
            )

            strikes = sorted({q.strike for q in chain})
            block = build_underlying_block(
                u,
                spec,
                spot,
                asof,
                config,
                anchor_expiries=_anchor_expiry_candidates(u, asof, spec),
                anchor_strikes=strikes,
                weekly_strikes=strikes,
                trades=trades,
                marks=marks,
                quote_bid=short_quote.quote.bid if short_quote else None,
                quote_ask=short_quote.quote.ask if short_quote else None,
                source="live",
            )
            block["positions"] = [p.as_dict() for p in positions]
            block["chain"] = [q.as_dict() for q in chain]
            block["quote"] = {
                "bid": spot_quote.bid,
                "ask": spot_quote.ask,
                "last": spot_quote.last,
            }
            payload["underlyings"].append(block)

    path = write_snapshot(payload, config.snapshot_dir, name=args.name)
    print(f"Wrote live snapshot to {path}")
    return 0


def _resolve_live_spec(
    u: Underlying, positions, chain, spot: float, asof: date
) -> tuple[StructureSpec | None, float]:
    """Infer the calendar from actual holdings.

    The anchor is the longest-dated long put; the income leg is the
    nearest-dated short put. Anything else in the account is ignored.
    """
    puts = [p for p in positions if p.kind == "put" and p.expiry]
    longs = [p for p in puts if p.quantity > 0]
    shorts = [p for p in puts if p.quantity < 0]
    if not longs:
        return None, spot

    anchor = max(longs, key=lambda p: p.expiry)
    short = min(shorts, key=lambda p: p.expiry) if shorts else None

    def iv_for(strike, expiry, fallback):
        q = next((c for c in chain if c.strike == strike and c.expiry == expiry), None)
        return q.iv if q and q.iv else fallback

    short_expiry = short.expiry if short else _next_friday(asof)
    short_strike = short.strike if short else spot

    return (
        StructureSpec(
            anchor_strike=anchor.strike,
            anchor_expiry=anchor.expiry,
            anchor_iv=iv_for(anchor.strike, anchor.expiry, 0.60),
            short_strike=short_strike,
            short_expiry=short_expiry,
            short_iv=iv_for(short_strike, short_expiry, 0.60),
            contracts=int(abs(anchor.quantity)),
            # avgCost from IBKR is already per contract including commissions.
            anchor_debit=anchor.avg_cost * anchor.quantity,
            short_credit=abs(short.avg_cost) / 100.0 if short else None,
            vol_beta=float((u.plan or {}).get("vol_beta", 1.5)),
        ),
        spot,
    )


def cmd_flex(config: Config, args: argparse.Namespace) -> int:
    from .flex import FlexError, fetch_statement, request_statement

    token = config.flex.token()
    if not token or not config.flex.query_id:
        print("Flex is not configured (need a token and flex.query_id).", file=sys.stderr)
        return 1
    try:
        reference, url = request_statement(token, config.flex.query_id)
        xml_text = fetch_statement(
            token, reference, url, config.flex.max_polls, config.flex.poll_seconds
        )
    except FlexError as exc:
        print(f"Flex error: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.write_text(xml_text)
    print(f"Wrote Flex statement to {out} ({len(xml_text):,} bytes)")
    return 0


def cmd_doctor(config: Config, args: argparse.Namespace) -> int:
    ok = True
    print(f"Underlyings   : {', '.join(config.symbols())}")

    for u in config.underlyings:
        state = "configured" if u.plan else "no [underlyings.plan] block (plan mode unavailable)"
        print(f"  {u.symbol:<6} {state}")

    host, port = config.tws.host, config.tws.port
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"TWS socket    : reachable at {host}:{port}")
    except OSError as exc:
        ok = False
        print(f"TWS socket    : UNREACHABLE at {host}:{port} ({exc})")
        print("                Start TWS or IB Gateway, then enable")
        print("                Configure > API > Settings > Enable ActiveX and Socket Clients.")

    if config.flex.token():
        print(f"Flex token    : present (from ${config.flex.token_env})")
    else:
        print(f"Flex token    : missing. export {config.flex.token_env}=...")
    print(f"Flex query id : {config.flex.query_id or 'not set'}")

    try:
        import ib_async  # noqa: F401

        print("ib_async      : installed")
    except ImportError:
        ok = False
        print("ib_async      : NOT installed (pip install -e .)")

    print(f"Snapshot dir  : {config.snapshot_dir.resolve()}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="balm", description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="check configuration and connectivity")
    p_doctor.set_defaults(func=cmd_doctor)

    p_plan = sub.add_parser("plan", help="model-only snapshot, no TWS required")
    p_plan.add_argument("--asof", help="YYYY-MM-DD, defaults to today")
    p_plan.add_argument("--name", default="latest")
    p_plan.set_defaults(func=cmd_plan)

    p_sync = sub.add_parser("sync", help="live snapshot from TWS and Flex")
    p_sync.add_argument("--flex-file", help="use a saved Flex XML instead of downloading")
    p_sync.add_argument("--name", default="latest")
    p_sync.set_defaults(func=cmd_sync)

    p_flex = sub.add_parser("flex", help="download the raw Flex statement")
    p_flex.add_argument("--out", default="data/flex-statement.xml")
    p_flex.set_defaults(func=cmd_flex)

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
