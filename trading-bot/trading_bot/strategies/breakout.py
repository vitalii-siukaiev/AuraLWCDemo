"""Volatility breakout: after a squeeze, buy a Donchian breakout confirmed by
volume. Volatility compression is usually followed by expansion."""
from __future__ import annotations

import pandas as pd

from ..regime import Regime
from .base import Action, Signal, Strategy


class SqueezeBreakout(Strategy):
    name = "squeeze_breakout"
    regimes = frozenset({Regime.SQUEEZE, Regime.RANGE, Regime.UPTREND})

    def entry(self, df: pd.DataFrame) -> Signal:
        r = df.iloc[-1]
        reasons, score = [], 0.0

        was_squeezed = (df.bbw_rank.iloc[-12:] < 0.25).any()
        if not was_squeezed:
            return Signal.hold(self.name, "no recent volatility squeeze")
        reasons.append("recent Bollinger squeeze")
        score += 0.25

        if not (r.close > r.dc_up):
            return Signal.hold(self.name, "no breakout above 20-bar high")
        reasons.append("close above 20-bar Donchian high")
        score += 0.3

        if r.volume > 1.5 * r.vol_sma:
            reasons.append(f"volume {r.volume / r.vol_sma:.1f}x average")
            score += 0.25
        else:
            return Signal.hold(self.name, "breakout without volume - likely fake")
        if r.close > r.ema50:
            reasons.append("above EMA50")
            score += 0.1
        if r.rsi < 75:
            score += 0.1

        stop = max(r.dc_lo, r.close - 2.0 * r.atr)
        stop = min(stop, r.bb_mid, r.close - 1.0 * r.atr)
        risk = r.close - stop
        target = r.close + 2.0 * risk
        return Signal(Action.BUY, min(score, 1.0), stop, target, reasons, self.name)

    def exit(self, df: pd.DataFrame, entry_price: float) -> Signal:
        r = df.iloc[-1]
        # Failed breakout: back inside the range below the midline
        if r.close < r.bb_mid and r.close < entry_price:
            return Signal(Action.SELL, 0.7, reasons=["breakout failed"], strategy=self.name)
        return Signal.hold(self.name)
