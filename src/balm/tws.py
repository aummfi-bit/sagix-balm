"""Live account and market data from TWS / IB Gateway via ib_async.

The connection is always opened read-only. This tool exists to observe and
model a position, never to place an order, and the read-only flag makes that
guarantee at the API boundary rather than by convention.

Two data sources are deliberately combined elsewhere in the package: this
module supplies live marks, greeks and today's fills, while ``flex`` supplies
the settled history. TWS only retains executions for the current session, so
neither source alone can reconstruct a multi-week campaign.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from .campaign import Trade
from .config import Config, Underlying
from .quotes import OptionQuote, Quote, closing_side

log = logging.getLogger(__name__)


@dataclass
class PositionRow:
    symbol: str
    sec_type: str
    quantity: float
    avg_cost: float  # per contract for options, per share for stock
    multiplier: float
    kind: str | None = None
    strike: float | None = None
    expiry: date | None = None
    con_id: int | None = None
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    # Set from the live chain: what flattening this leg actually prices at.
    exit_price: float | None = None

    def apply_quote(self, quote: "Quote | None") -> None:
        """Price the exit off the side that would trade, if it is quoted."""
        self.exit_price = quote.to_close(self.quantity) if quote else None

    @property
    def exit_value(self) -> float | None:
        """Signed cash from closing the position, ``None`` without a quote."""
        if self.exit_price is None:
            return None
        return self.exit_price * self.quantity * self.multiplier

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "secType": self.sec_type,
            "quantity": self.quantity,
            "avgCost": self.avg_cost,
            "multiplier": self.multiplier,
            "kind": self.kind,
            "strike": self.strike,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "conId": self.con_id,
            # IBKR's own mark, which is mid-derived and therefore not a fill.
            "marketPrice": self.market_price,
            "marketValue": self.market_value,
            "unrealizedPnl": self.unrealized_pnl,
            "closingSide": closing_side(self.quantity),
            "exitPrice": self.exit_price,
            "exitValue": self.exit_value,
        }


def _clean(x: Any) -> float | None:
    """IBKR encodes missing numerics as NaN or -1; normalize both to None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or v <= -1:
        return None
    return v


def _parse_ib_date(raw: str) -> date:
    return datetime.strptime(raw[:8], "%Y%m%d").date()


class TwsClient:
    """Thin wrapper over ib_async, scoped to what the campaign tracker needs."""

    def __init__(self, config: Config):
        self.config = config
        self._ib = None

    def __enter__(self) -> "TwsClient":
        from ib_async import IB

        self._ib = IB()
        cfg = self.config.tws
        self._ib.connect(
            cfg.host,
            cfg.port,
            clientId=cfg.client_id,
            readonly=True,
            timeout=20,
        )
        # Frozen data keeps last-known marks available outside RTH, which is
        # when most position review actually happens.
        self._ib.reqMarketDataType(cfg.market_data_type)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    @property
    def ib(self):
        if self._ib is None:
            raise RuntimeError("TwsClient must be used as a context manager.")
        return self._ib

    def account(self) -> str | None:
        if self.config.tws.account:
            return self.config.tws.account
        accounts = self.ib.managedAccounts()
        return accounts[0] if accounts else None

    def account_values(self) -> dict[str, float]:
        """Balances that bound how much short-put exposure is survivable."""
        wanted = {
            "NetLiquidation",
            "TotalCashValue",
            "BuyingPower",
            "ExcessLiquidity",
            "MaintMarginReq",
            "InitMarginReq",
            "AvailableFunds",
            "GrossPositionValue",
        }
        out: dict[str, float] = {}
        for row in self.ib.accountSummary(self.account() or ""):
            if row.tag in wanted and row.currency in ("USD", ""):
                try:
                    out[row.tag] = float(row.value)
                except ValueError:
                    continue
        return out

    def stock_contract(self, u: Underlying):
        from ib_async import Stock

        contract = Stock(
            u.symbol,
            u.exchange,
            u.currency,
            primaryExchange=u.primary_exchange or "",
        )
        self.ib.qualifyContracts(contract)
        return contract

    def spot(self, contract) -> Quote:
        ticker = self.ib.reqMktData(contract, "", False, False)
        self._await_ticks(ticker, need_greeks=False)
        q = Quote(
            bid=_clean(ticker.bid),
            ask=_clean(ticker.ask),
            last=_clean(ticker.last),
            close=_clean(ticker.close),
        )
        self.ib.cancelMktData(contract)
        return q

    def _await_ticks(self, ticker, need_greeks: bool) -> None:
        """Block until the ticker has usable data or the timeout elapses.

        Greeks arrive on a separate tick from the quote and are frequently
        the slower of the two, so they get their own readiness condition.
        """
        deadline = self.config.tws.market_data_timeout
        waited = 0.0
        step = 0.25
        while waited < deadline:
            if need_greeks and ticker.modelGreeks is not None:
                return
            if not need_greeks and (
                _clean(ticker.last) or _clean(ticker.close) or _clean(ticker.bid)
            ):
                return
            self.ib.sleep(step)
            waited += step

    def option_chain(
        self,
        u: Underlying,
        stock_contract,
        spot: float,
        max_expirations: int,
        strike_window: float,
        rights: tuple[str, ...] = ("P",),
    ) -> list[OptionQuote]:
        """Pull near-the-money options across the next several expirations.

        ``rights`` is ("P",) for the put campaign alone, or ("P", "C") when the
        call side is wanted too. Each right doubles the number of contracts in
        flight, so the caller decides.
        """
        from ib_async import Option

        params = self.ib.reqSecDefOptParams(
            stock_contract.symbol, "", stock_contract.secType, stock_contract.conId
        )
        chain = next(
            (p for p in params if p.exchange == "SMART" and p.tradingClass == u.symbol),
            None,
        ) or next((p for p in params if p.exchange == "SMART"), None)
        if chain is None:
            log.warning("No SMART option chain returned for %s", u.symbol)
            return []

        today = date.today()
        expirations = sorted(e for e in chain.expirations if _parse_ib_date(e) >= today)
        expirations = expirations[:max_expirations]

        lo, hi = spot * (1 - strike_window), spot * (1 + strike_window)
        strikes = sorted(k for k in chain.strikes if lo <= k <= hi)
        if not expirations or not strikes:
            return []

        contracts = [
            Option(u.symbol, exp, k, right, "SMART", tradingClass=chain.tradingClass)
            for exp in expirations
            for k in strikes
            for right in rights
        ]
        qualified = [c for c in self.ib.qualifyContracts(*contracts) if c.conId]

        tickers = [self.ib.reqMktData(c, "", False, False) for c in qualified]
        # One shared wait: requesting hundreds of contracts serially would take
        # minutes, so they are all in flight before we start polling.
        deadline = self.config.tws.market_data_timeout
        waited = 0.0
        while waited < deadline:
            if all(t.modelGreeks is not None for t in tickers):
                break
            self.ib.sleep(0.5)
            waited += 0.5

        quotes: list[OptionQuote] = []
        for contract, ticker in zip(qualified, tickers):
            g = ticker.modelGreeks
            quotes.append(
                OptionQuote(
                    symbol=u.symbol,
                    kind={"P": "put", "C": "call"}.get(contract.right, "put"),
                    strike=contract.strike,
                    expiry=_parse_ib_date(contract.lastTradeDateOrContractMonth),
                    con_id=contract.conId,
                    quote=Quote(
                        bid=_clean(ticker.bid),
                        ask=_clean(ticker.ask),
                        last=_clean(ticker.last),
                        close=_clean(ticker.close),
                    ),
                    iv=_clean(g.impliedVol) if g else None,
                    delta=g.delta if g and g.delta is not None else None,
                    gamma=g.gamma if g and g.gamma is not None else None,
                    theta=g.theta if g and g.theta is not None else None,
                    vega=g.vega if g and g.vega is not None else None,
                    undPrice=_clean(g.undPrice) if g else None,
                )
            )
            self.ib.cancelMktData(contract)

        return quotes

    def positions(self, symbols: Iterable[str]) -> list[PositionRow]:
        wanted = set(symbols)
        rows: list[PositionRow] = []
        for item in self.ib.portfolio(self.account() or ""):
            c = item.contract
            if c.symbol not in wanted:
                continue
            try:
                multiplier = float(c.multiplier) if c.multiplier else 1.0
            except ValueError:
                multiplier = 1.0
            rows.append(
                PositionRow(
                    symbol=c.symbol,
                    sec_type=c.secType,
                    quantity=item.position,
                    avg_cost=item.averageCost,
                    multiplier=multiplier,
                    kind={"P": "put", "C": "call"}.get(c.right or "", None),
                    strike=c.strike or None,
                    expiry=_parse_ib_date(c.lastTradeDateOrContractMonth)
                    if c.lastTradeDateOrContractMonth
                    else None,
                    con_id=c.conId,
                    market_price=_clean(item.marketPrice),
                    market_value=item.marketValue,
                    unrealized_pnl=item.unrealizedPNL,
                )
            )
        return rows

    def todays_trades(self, symbols: Iterable[str]) -> list[Trade]:
        """Fills from the current session.

        Flex statements lag by a day, so without this a roll executed today
        would be invisible to the campaign accounting until tomorrow.
        """
        wanted = set(symbols)
        trades: list[Trade] = []
        for fill in self.ib.fills():
            c, ex = fill.contract, fill.execution
            if c.symbol not in wanted or c.secType != "OPT":
                continue
            qty = ex.shares if ex.side.upper() == "BOT" else -ex.shares
            commission = 0.0
            if fill.commissionReport and fill.commissionReport.commission:
                commission = -abs(fill.commissionReport.commission)
            trades.append(
                Trade(
                    symbol=c.symbol,
                    trade_date=ex.time.date(),
                    quantity=qty,
                    price=ex.price,
                    multiplier=float(c.multiplier or 100),
                    commission=commission,
                    kind={"P": "put", "C": "call"}.get(c.right or "", None),
                    strike=c.strike or None,
                    expiry=_parse_ib_date(c.lastTradeDateOrContractMonth),
                    trade_id=ex.execId,
                )
            )
        return trades
