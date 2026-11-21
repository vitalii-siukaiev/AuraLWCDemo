"""Donchian trend breakout (turtle-style): buy a new 55-bar high in a
trending market and let the trailing stop ride the trend. Few trades, low
win rate, large winners - one of the longest-documented systematic edges
(time-series momentum)."""
from __future__ import annotations

import pandas as pd

from ..regime import Regime
from .base import Action, Signal, Strategy


class DonchianTrend(Strategy):
    name = "donchian_trend"
    regimes = frozenset({Regime.STRONG_UPTREND, Regime.UPTREND})

    def entry(self, df: pd.DataFrame) -> Signal:
        r, p = df.iloc[-1], df.iloc[-2]
        if not (r.close > r.dc55_up and p.close <= p.dc55_up):
            return Signal.hold(self.name, "no fresh 55-bar high")
        reasons, score = ["fresh 55-bar breakout"], 0.35
        if r.adx >= 20:
            reasons.append(f"ADX {r.adx:.0f} confirms trend")
            score += 0.2
        else:
            return Signal.hold(self.name, "breakout without trend strength")
        if r.ema21 > r.ema50 > r.ema200:
            reasons.append("EMAs fully stacked")
            score += 0.2
        if r.volume > 1.2 * r.vol_sma:
            reasons.append("above-average volume")
            score += 0.15
        if r.rsi < 80:
            score += 0.1
        stop = r.close - 2.5 * r.atr
        target = r.close + 5.0 * (r.close - stop)   # far target: the trailing stop does the exiting
        return Signal(Action.BUY, min(score, 1.0), stop, target, reasons, self.name)

    def exit(self, df: pd.DataFrame, entry_price: float) -> Signal:
        r = df.iloc[-1]
        lo20 = df.low.iloc[-21:-1].min()
        if r.close < lo20:
            return Signal(Action.SELL, 0.8, reasons=["close below 20-bar low - trend over"], strategy=self.name)
        return Signal.hold(self.name)
