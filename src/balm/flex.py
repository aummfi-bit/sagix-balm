"""IBKR Flex Web Service client.

TWS only exposes executions from the current session, so the multi-week trade
history a campaign depends on has to come from a Flex statement. This is the
documented two-step protocol: request a statement to get a reference code,
then poll for the generated report.

Set up in Client Portal under Performance & Reports > Flex Queries. Create a
Trades query that includes at minimum: underlyingSymbol, assetCategory,
putCall, strike, expiry, quantity, tradePrice, ibCommission, tradeDate,
multiplier, notes and tradeID.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import requests

from .campaign import Trade
from .config import FlexConfig

log = logging.getLogger(__name__)

SEND_REQUEST_URL = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
)
GET_STATEMENT_URL = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
)
# IBKR rejects requests without a User-Agent.
HEADERS = {"User-Agent": "sagix-balm/0.1 (+https://localhost)"}

# Statement is queued but not yet generated; the documented retry path.
_STILL_GENERATING = "1019"


class FlexError(RuntimeError):
    pass


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip().replace("-", "")
    if len(raw) < 8:
        return None
    try:
        return datetime.strptime(raw[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _parse_float(raw: str | None, default: float | None = None) -> float | None:
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def request_statement(token: str, query_id: str) -> tuple[str, str]:
    """Step one: exchange the query id for a reference code."""
    resp = requests.get(
        SEND_REQUEST_URL,
        params={"t": token, "q": query_id, "v": "3"},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    status = (root.findtext("Status") or "").strip()
    if status != "Success":
        code = root.findtext("ErrorCode") or "?"
        message = root.findtext("ErrorMessage") or "unknown error"
        raise FlexError(f"Flex SendRequest failed ({code}): {message}")

    reference = root.findtext("ReferenceCode")
    url = root.findtext("Url") or GET_STATEMENT_URL
    if not reference:
        raise FlexError("Flex SendRequest returned no reference code.")
    return reference, url


def fetch_statement(
    token: str,
    reference: str,
    url: str = GET_STATEMENT_URL,
    max_polls: int = 8,
    poll_seconds: float = 5.0,
) -> str:
    """Step two: poll until the statement is generated, then return its XML."""
    for attempt in range(max_polls):
        resp = requests.get(
            url,
            params={"t": token, "q": reference, "v": "3"},
            headers=HEADERS,
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.text

        if "<FlexQueryResponse" in text:
            return text

        # Anything else is a status envelope: either "still generating" or a
        # hard failure that will not resolve by waiting.
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise FlexError(f"Unparseable Flex response: {exc}") from exc

        code = (root.findtext("ErrorCode") or "").strip()
        message = root.findtext("ErrorMessage") or "unknown error"
        if code != _STILL_GENERATING:
            raise FlexError(f"Flex GetStatement failed ({code or '?'}): {message}")

        log.info("Flex statement still generating (attempt %d/%d)", attempt + 1, max_polls)
        time.sleep(poll_seconds)

    raise FlexError(f"Flex statement not ready after {max_polls} polls.")


def parse_trades(xml_text: str, symbols: set[str] | None = None) -> list[Trade]:
    """Convert Flex ``<Trade>`` rows into normalized trades.

    Only option rows are returned. Flex reports the OSI contract string in
    ``symbol``, so grouping keys come from ``underlyingSymbol`` instead.
    """
    root = ET.fromstring(xml_text)
    trades: list[Trade] = []

    for node in root.iter("Trade"):
        get = node.get
        if (get("assetCategory") or "").upper() != "OPT":
            continue

        underlying = get("underlyingSymbol") or get("symbol") or ""
        if symbols is not None and underlying not in symbols:
            continue

        expiry = _parse_date(get("expiry"))
        traded = _parse_date(get("tradeDate")) or _parse_date(get("dateTime"))
        strike = _parse_float(get("strike"))
        price = _parse_float(get("tradePrice"))
        quantity = _parse_float(get("quantity"))
        if expiry is None or traded is None or strike is None or price is None or quantity is None:
            log.warning("Skipping incomplete Flex trade row: %s", get("tradeID"))
            continue

        # Flex signs quantity, but fall back to buySell if a query omits it.
        if quantity > 0 and (get("buySell") or "").upper() == "SELL":
            quantity = -quantity

        right = (get("putCall") or "").upper()
        kind = {"P": "put", "C": "call"}.get(right)
        if kind is None:
            continue

        commission = _parse_float(get("ibCommission"), 0.0) or 0.0

        trades.append(
            Trade(
                symbol=underlying,
                trade_date=traded,
                quantity=quantity,
                price=price,
                multiplier=_parse_float(get("multiplier"), 100.0) or 100.0,
                commission=-abs(commission),
                kind=kind,
                strike=strike,
                expiry=expiry,
                notes=get("notes") or get("code") or "",
                trade_id=get("tradeID") or "",
            )
        )

    return trades


def download_trades(config: FlexConfig, symbols: set[str] | None = None) -> list[Trade]:
    token = config.token()
    if not token:
        raise FlexError(
            f"No Flex token. Export {config.token_env} or set flex.token_literal in config.toml."
        )
    if not config.query_id:
        raise FlexError("No flex.query_id configured.")

    reference, url = request_statement(token, config.query_id)
    xml_text = fetch_statement(
        token, reference, url, max_polls=config.max_polls, poll_seconds=config.poll_seconds
    )
    return parse_trades(xml_text, symbols)


def load_trades_file(path: Path | str, symbols: set[str] | None = None) -> list[Trade]:
    """Parse a Flex XML statement already saved to disk.

    Useful for backtesting the accounting against a known history, and as an
    escape hatch when the Flex token has expired.
    """
    return parse_trades(Path(path).read_text(), symbols)


def merge_trades(*groups: list[Trade]) -> list[Trade]:
    """Combine trade lists, de-duplicating on execution id.

    Today's fills from TWS overlap with tomorrow's Flex statement, so the two
    sources must be reconciled rather than concatenated. Rows without an id
    are kept, since a hand-entered trade has no execution to collide with.
    """
    seen: set[str] = set()
    out: list[Trade] = []
    for group in groups:
        for t in group:
            if t.trade_id:
                if t.trade_id in seen:
                    continue
                seen.add(t.trade_id)
            out.append(t)
    return sorted(out, key=lambda t: (t.trade_date, t.symbol, t.expiry or date.min))
