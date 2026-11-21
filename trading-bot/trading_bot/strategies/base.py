from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from ..regime import Regime


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"   # close an open long
    HOLD = "hold"


@dataclass
class Signal:
    action: Action
    confidence: float = 0.0          # 0..1
    stop: float | None = None        # protective stop price
    target: float | None = None      # take-profit price
    reasons: list[str] = field(default_factory=list)
    strategy: str = ""

    @staticmethod
    def hold(strategy: str, *reasons: str) -> "Signal":
        return Signal(Action.HOLD, 0.0, reasons=list(reasons), strategy=strategy)


class Strategy(ABC):
    """A strategy looks at the latest bars (with indicators) and returns a
    Signal. It never sizes positions - that's the risk manager's job."""

    name: str = "base"
    #: Regimes this strategy is designed for. The selector only consults a
    #: strategy when the current regime is in this set.
    regimes: frozenset[Regime] = frozenset()
    #: Only take long entries when the higher-timeframe trend agrees.
    needs_htf_trend: bool = True

    @abstractmethod
    def entry(self, df: pd.DataFrame) -> Signal: ...

    def exit(self, df: pd.DataFrame, entry_price: float) -> Signal:
        """Strategy-specific discretionary exit (in addition to stop/target)."""
        return Signal.hold(self.name)
