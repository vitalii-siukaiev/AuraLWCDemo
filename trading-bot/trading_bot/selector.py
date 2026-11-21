"""Dynamic strategy selection.

Two layers decide which strategy gets to trade:

1. **Regime fit** - only strategies built for the current regime are asked.
2. **Recent performance** - every strategy keeps a rolling record (in R
   multiples) of its real trades *and* shadow trades (signals it produced
   while benched or not chosen). A strategy whose recent expectancy turns
   negative is benched; it keeps paper-trading in the shadows and is
   reinstated once its shadow record recovers. The market changes character;
   the bot adapts by trusting what has been working lately.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import pandas as pd

from .regime import Regime, RegimeReading
from .strategies import ALL_STRATEGIES, Action, Signal, Strategy


@dataclass
class ShadowTrade:
    entry: float
    stop: float
    target: float
    bars: int = 0


@dataclass
class StrategyStats:
    window: int = 12
    results: deque = field(default_factory=lambda: deque(maxlen=12))  # R multiples
    shadows: list[ShadowTrade] = field(default_factory=list)
    benched: bool = False

    def record(self, r_multiple: float) -> None:
        self.results.append(r_multiple)

    @property
    def expectancy(self) -> float:
        return sum(self.results) / len(self.results) if self.results else 0.0

    @property
    def win_rate(self) -> float:
        return sum(1 for r in self.results if r > 0) / len(self.results) if self.results else 0.0

    def multiplier(self) -> float:
        """Confidence multiplier from track record. Neutral (1.0) with no
        history; ranges roughly 0.5 .. 1.3."""
        n = len(self.results)
        if n < 3:
            return 1.0
        return max(0.5, min(1.3, 1.0 + 0.3 * self.expectancy))


class StrategySelector:
    MIN_TRADES_TO_BENCH = 5
    BENCH_EXPECTANCY = -0.25   # R
    REINSTATE_EXPECTANCY = 0.15

    def __init__(self, strategies: list[Strategy] | None = None):
        self.strategies = strategies or [cls() for cls in ALL_STRATEGIES]
        self.stats: dict[str, StrategyStats] = {s.name: StrategyStats() for s in self.strategies}

    def by_name(self, name: str) -> Strategy:
        return next(s for s in self.strategies if s.name == name)

    # ---- performance bookkeeping -------------------------------------
    def record_trade(self, name: str, r_multiple: float) -> None:
        st = self.stats[name]
        st.record(r_multiple)
        self._update_bench(name)

    def _update_bench(self, name: str) -> None:
        st = self.stats[name]
        if not st.benched and len(st.results) >= self.MIN_TRADES_TO_BENCH and st.expectancy < self.BENCH_EXPECTANCY:
            st.benched = True
        elif st.benched and st.expectancy > self.REINSTATE_EXPECTANCY:
            st.benched = False

    def update_shadows(self, bar: pd.Series) -> None:
        """Resolve open shadow trades against the latest bar."""
        for name, st in self.stats.items():
            still_open = []
            for sh in st.shadows:
                sh.bars += 1
                risk = sh.entry - sh.stop
                if bar.low <= sh.stop:
                    st.record(-1.0)
                elif bar.high >= sh.target:
                    st.record((sh.target - sh.entry) / risk)
                elif sh.bars > 60:
                    st.record((bar.close - sh.entry) / risk)
                else:
                    still_open.append(sh)
                    continue
                self._update_bench(name)
            st.shadows = still_open

    # ---- selection ---------------------------------------------------
    def evaluate(self, df: pd.DataFrame, reading: RegimeReading) -> tuple[Signal | None, list[Signal]]:
        """Return (best actionable signal or None, all signals for logging)."""
        signals: list[Signal] = []
        best: Signal | None = None
        best_score = 0.0
        for strat in self.strategies:
            if reading.regime not in strat.regimes:
                continue
            if strat.needs_htf_trend and not reading.htf_bullish:
                signals.append(Signal.hold(strat.name, "higher timeframe not bullish"))
                continue
            sig = strat.entry(df)
            signals.append(sig)
            if sig.action != Action.BUY or sig.stop is None or sig.target is None:
                continue
            st = self.stats[strat.name]
            if st.benched:
                # Paper-trade it so it can earn its way back.
                st.shadows.append(ShadowTrade(df.close.iloc[-1], sig.stop, sig.target))
                sig.reasons.append("BENCHED - shadow trading only")
                continue
            score = sig.confidence * st.multiplier()
            # Regime alignment bonus: trend strategies in strong trends, etc.
            if strat.name == "trend_pullback" and reading.regime == Regime.STRONG_UPTREND:
                score *= 1.1
            if strat.name == "squeeze_breakout" and reading.regime == Regime.SQUEEZE:
                score *= 1.1
            sig.confidence = min(1.0, score)
            if score > best_score:
                best, best_score = sig, score
        # Signals not chosen become shadow trades so all strategies keep a
        # live track record.
        for sig in signals:
            if sig is not best and sig.action == Action.BUY and sig.stop and sig.target \
                    and not self.stats[sig.strategy].benched:
                self.stats[sig.strategy].shadows.append(ShadowTrade(df.close.iloc[-1], sig.stop, sig.target))
        return best, signals

    def summary(self) -> dict:
        return {
            n: {"trades": len(s.results), "expectancy_R": round(s.expectancy, 2),
                "win_rate": round(s.win_rate, 2), "benched": s.benched}
            for n, s in self.stats.items()
        }

    # ---- persistence -------------------------------------------------
    def to_dict(self) -> dict:
        return {n: {"results": list(s.results), "benched": s.benched} for n, s in self.stats.items()}

    def load_dict(self, d: dict) -> None:
        for n, v in d.items():
            if n in self.stats:
                self.stats[n].results.extend(v.get("results", []))
                self.stats[n].benched = v.get("benched", False)
