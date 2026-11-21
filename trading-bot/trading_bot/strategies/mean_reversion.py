"""Mean reversion: buy oversold dips at the lower Bollinger band in a range.

Only valid when there is no strong trend (ADX low) - otherwise "oversold"
just keeps getting more oversold.
"""
from __future__ import annotations

import pandas as pd

from ..regime import Regime
from .base import Action, Signal, Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"
    regimes = frozenset({Regime.RANGE})
    needs_htf_trend = False  # ranges are often counter-trend on the HTF

    def entry(self, df: pd.DataFrame) -> Signal:
        r, p = df.iloc[-1], df.iloc[-2]
        reasons, score = [], 0.0

        if r.adx > 25:
            return Signal.hold(self.name, "ADX too high for mean reversion")
        if not (p.close <= p.bb_lo or p.bb_pctb < 0.05 or r.bb_pctb < 0.1):
            return Signal.hold(self.name, "price not at lower band")
        reasons.append("price at/below lower Bollinger band")
        score += 0.3

        if p.rsi < 33 and r.rsi > p.rsi:
            reasons.append(f"RSI turning up from oversold ({p.rsi:.0f}->{r.rsi:.0f})")
            score += 0.3
        elif r.rsi > 40:
            return Signal.hold(self.name, "RSI not oversold")

        if r.stoch_k > r.stoch_d and p.stoch_k <= p.stoch_d and r.stoch_k < 30:
            reasons.append("stochastic bullish cross in oversold zone")
            score += 0.2
        if r.close > r.open:
            reasons.append("bullish reversal candle")
            score += 0.2
        else:
            return Signal.hold(self.name, "no reversal candle yet (don't catch a falling knife)")

        stop = min(df.low.iloc[-3:].min(), r.close) - 0.8 * r.atr
        target = r.bb_mid  # revert to the mean
        return Signal(Action.BUY, min(score, 1.0), stop, target, reasons, self.name)

    def exit(self, df: pd.DataFrame, entry_price: float) -> Signal:
        r = df.iloc[-1]
        if r.bb_pctb > 0.9 or r.rsi > 68:
            return Signal(Action.SELL, 0.7, reasons=["reached upper band / RSI high"], strategy=self.name)
        if r.adx > 30 and r.mdi > r.pdi:
            return Signal(Action.SELL, 0.8, reasons=["range broke into downtrend"], strategy=self.name)
        return Signal.hold(self.name)
