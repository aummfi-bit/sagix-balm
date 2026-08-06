"""Configuration loading.

Nothing here is a credential. The only external service the package talks to
is a public quote feed, so a config file can be committed without thinking
twice about it.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.toml")

T = TypeVar("T")


def _known(cls: type[T], raw: dict[str, Any]) -> T:
    """Build ``cls`` from ``raw``, ignoring keys it no longer has.

    Settings get removed as the tool changes -- the whole Interactive Brokers
    surface went at once -- and an existing config file should not become a
    crash. Unknown keys are noted and dropped.
    """
    accepted = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    extra = set(raw) - accepted
    if extra:
        log.debug("Ignoring unused config keys for %s: %s", cls.__name__, ", ".join(sorted(extra)))
    return cls(**{k: v for k, v in raw.items() if k in accepted})


@dataclass
class ModelConfig:
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    binomial_steps: int = 240
    # How far around the money to pull chain strikes, as a fraction of spot.
    strike_window: float = 0.25
    # Number of forward expirations to pull per underlying.
    expirations: int = 8
    # Seconds to wait on the delayed-quote feed.
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
    label: str | None = None
    # Optional [underlyings.plan] table naming the structure to model. Keys
    # mirror StructureSpec plus a `spot` used only by `balm plan`; `balm
    # quotes` takes spot and volatility from the feed instead.
    plan: dict = field(default_factory=dict)

    def display(self) -> str:
        return self.label or self.symbol


@dataclass
class Config:
    underlyings: list[Underlying] = field(default_factory=list)
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

        underlyings = [_known(Underlying, u) for u in raw.get("underlyings", [])]
        if not underlyings:
            raise ValueError(f"{path} defines no [[underlyings]] entries.")

        for gone in ("tws", "flex"):
            if gone in raw:
                log.info(
                    "[%s] in %s is ignored: prices now come from the public "
                    "quote feed, which needs no account.",
                    gone,
                    path,
                )

        return cls(
            underlyings=underlyings,
            model=_known(ModelConfig, raw.get("model", {})),
            rules=_known(RulesConfig, raw.get("rules", {})),
            snapshot_dir=Path(raw.get("snapshot_dir", "data/snapshots")),
        )

    def symbols(self) -> list[str]:
        return [u.symbol for u in self.underlyings]
