"""Configuration loading.

The Flex token is a bearer credential for your whole account history, so it is
read from the environment by default and only falls back to the file when you
explicitly opt in.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")


@dataclass
class TwsConfig:
    host: str = "127.0.0.1"
    # 7497 is TWS paper, 7496 TWS live, 4002 Gateway paper, 4001 Gateway live.
    port: int = 7497
    client_id: int = 17
    account: str | None = None
    # Wait this long for the greeks in a model-computed tick to arrive.
    market_data_timeout: float = 12.0
    # 1 = live, 2 = frozen, 3 = delayed, 4 = delayed-frozen. Frozen fallback
    # keeps the tool usable outside regular trading hours.
    market_data_type: int = 2


@dataclass
class FlexConfig:
    query_id: str | None = None
    token_env: str = "IBKR_FLEX_TOKEN"
    token_literal: str | None = None
    max_polls: int = 8
    poll_seconds: float = 5.0

    def token(self) -> str | None:
        return os.environ.get(self.token_env) or self.token_literal


@dataclass
class ModelConfig:
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    binomial_steps: int = 240
    # How far around the money to pull chain strikes, as a fraction of spot.
    strike_window: float = 0.25
    # Number of forward expirations to pull per underlying.
    expirations: int = 8
    # Also pull the call chain, so the desk's call panel has real quotes behind
    # it. Doubles the contracts requested per sync; turn off if that is slow.
    include_calls: bool = True
    # Seconds to wait on the delayed-quote feed used by `balm quotes`.
    quote_timeout: float = 20.0


@dataclass
class RulesConfig:
    """Operating rules, surfaced in the canvas as a per-roll checklist."""

    # Weekday (Mon=0) after which an open short weekly must be rolled or closed.
    roll_by_weekday: int = 2
    # Close the short once this share of the sold extrinsic has been harvested.
    extrinsic_harvest_target: float = 0.80
    # Target absolute delta for a newly sold short put.
    short_delta_target: float = 0.40
    short_delta_max: float = 0.55
    # Warn when the short's early-exercise premium falls under this many cents.
    assignment_warn_premium: float = 0.10
    # Warn when the bid/ask spread exceeds this fraction of the mid.
    max_spread_pct: float = 0.10


@dataclass
class Underlying:
    symbol: str
    exchange: str = "SMART"
    currency: str = "USD"
    primary_exchange: str | None = None
    label: str | None = None
    # Optional [underlyings.plan] table describing the structure to model when
    # running without TWS. Keys mirror StructureSpec plus a `spot` override.
    plan: dict = field(default_factory=dict)

    def display(self) -> str:
        return self.label or self.symbol


@dataclass
class Config:
    underlyings: list[Underlying] = field(default_factory=list)
    tws: TwsConfig = field(default_factory=TwsConfig)
    flex: FlexConfig = field(default_factory=FlexConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    snapshot_dir: Path = Path("data/snapshots")

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Copy config.example.toml to {path} and edit it."
            )
        raw = tomllib.loads(path.read_text())

        underlyings = [Underlying(**u) for u in raw.get("underlyings", [])]
        if not underlyings:
            raise ValueError(f"{path} defines no [[underlyings]] entries.")

        return cls(
            underlyings=underlyings,
            tws=TwsConfig(**raw.get("tws", {})),
            flex=FlexConfig(**raw.get("flex", {})),
            model=ModelConfig(**raw.get("model", {})),
            rules=RulesConfig(**raw.get("rules", {})),
            snapshot_dir=Path(raw.get("snapshot_dir", "data/snapshots")),
        )

    def symbols(self) -> list[str]:
        return [u.symbol for u in self.underlyings]
