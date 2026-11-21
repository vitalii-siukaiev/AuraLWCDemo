"""Trend following: buy pullbacks inside an established uptrend.

Pro logic: don't chase - wait for price to pull back to the fast EMA zone in
an uptrend, then enter when momentum (MACD histogram) turns back up.
"""
from __future__ import annotations

import pandas as pd

from ..regime import Regime
from .base import Action, Signal, Strategy


class TrendPullback(Strategy):
    name = "trend_pullback"
    regimes = frozenset({Regime.STRONG_UPTREND, Regime.UPTREND})

    def entry(self, df: pd.DataFrame) -> Signal:
        r, p = df.iloc[-1], df.iloc[-2]
        reasons, score = [], 0.0

        if not (r.ema21 > r.ema50):
            return Signal.hold(self.name, "EMA21 below EMA50")
        # Pullback: in the last 5 bars price touched the EMA21 zone
        recent = df.iloc[-5:]
        touched = (recent.low <= recent.ema21 * 1.003).any()
        if not touched:
            return Signal.hold(self.name, "no pullback to EMA21 yet (don't chase)")
        reasons.append("pullback to EMA21 in uptrend")
        score += 0.3

        if r.close > r.ema9 and r.close > r.open:
            reasons.append("bullish close back above EMA9")
            score += 0.2
        else:
            return Signal.hold(self.name, "waiting for bullish confirmation candle")

        if r.macd_hist > p.macd_hist:
            reasons.append("MACD histogram rising")
            score += 0.2
        if 40 <= r.rsi <= 68:
            reasons.append(f"RSI {r.rsi:.0f} healthy (not overbought)")
            score += 0.15
        elif r.rsi > 72:
            return Signal.hold(self.name, f"RSI {r.rsi:.0f} overbought")
        if r.obv > r.obv_ema:
            reasons.append("OBV confirms accumulation")
            score += 0.15

        swing_low = recent.low.min()
        stop = min(swing_low - 0.3 * r.atr, r.close - 1.5 * r.atr)
        risk = r.close - stop
        target = r.close + 2.5 * risk
        return Signal(Action.BUY, min(score, 1.0), stop, target, reasons, self.name)

    def exit(self, df: pd.DataFrame, entry_price: float) -> Signal:
        r = df.iloc[-1]
        if r.close < r.ema50:
            return Signal(Action.SELL, 0.8, reasons=["close below EMA50 - trend broken"], strategy=self.name)
        if r.macd < r.macd_sig and r.close < r.ema21 and r.close > entry_price:
            return Signal(Action.SELL, 0.6, reasons=["momentum lost, locking profit"], strategy=self.name)
        return Signal.hold(self.name)
