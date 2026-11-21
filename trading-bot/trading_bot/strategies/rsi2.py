"""RSI(2) pullback (Connors-style): in a healthy uptrend, buy a sharp
2-bar oversold dip and sell into the bounce. High win rate, small wins."""
from __future__ import annotations

import pandas as pd

from ..regime import Regime
from .base import Action, Signal, Strategy


class RSI2Pullback(Strategy):
    name = "rsi2_pullback"
    regimes = frozenset({Regime.STRONG_UPTREND, Regime.UPTREND})

    def entry(self, df: pd.DataFrame) -> Signal:
        r, p = df.iloc[-1], df.iloc[-2]
        if not (r.close > r.ema200 and r.ema50 > r.ema200):
            return Signal.hold(self.name, "not above EMA200")
        if not (p.rsi2 < 10):
            return Signal.hold(self.name, "RSI(2) not deeply oversold")
        reasons, score = [f"RSI(2) dipped to {p.rsi2:.0f} in uptrend"], 0.35
        if r.close > r.open and r.rsi2 > p.rsi2:
            reasons.append("bounce candle")
            score += 0.25
        else:
            return Signal.hold(self.name, "waiting for bounce candle")
        if r.close > r.ema50:
            reasons.append("still above EMA50")
            score += 0.2
        if r.obv > r.obv_ema:
            reasons.append("OBV holding up")
            score += 0.1
        stop = min(df.low.iloc[-3:].min() - 0.5 * r.atr, r.close - 1.5 * r.atr)
        target = r.close + 1.8 * (r.close - stop)
        return Signal(Action.BUY, min(score, 1.0), stop, target, reasons, self.name)

    def exit(self, df: pd.DataFrame, entry_price: float) -> Signal:
        r = df.iloc[-1]
        if r.rsi2 > 85 and r.close > entry_price:
            return Signal(Action.SELL, 0.7, reasons=["RSI(2) overbought - take the bounce"], strategy=self.name)
        return Signal.hold(self.name)
