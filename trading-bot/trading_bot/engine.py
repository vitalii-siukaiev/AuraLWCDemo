"""The trading engine: one decision cycle per closed candle.

Cycle order (same in backtest, paper and live):
  1. Mark-to-market, charge funding (perps), update risk state.
  2. If a position is open: check stop / target, update break-even and
     trailing stop, ask the owning strategy for a discretionary exit, and
     exit if the regime turned hostile.
  3. If flat: detect regime, ask fitting strategies for signals on the long
     side (and, on perps, the short side), pick the best one (dynamic
     selector), size it through the risk manager, enter.

Shorts use a *mirror view*: the price series is inverted (p -> K/p, highs and
lows swapped), which turns a downtrend into an uptrend. Every long strategy,
stop rule and trailing rule then works unchanged as a short strategy on the
mirrored data, so long and short logic can never drift apart. Positions are
managed in their own "side space"; only fills and P&L use real prices.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from .indicators import add_all
from .regime import Regime, classify, higher_timeframe_trend
from .risk import RiskConfig, RiskManager
from .selector import StrategySelector
from .strategies import ALL_STRATEGIES, Action

log = logging.getLogger("bot")

HOSTILE = {Regime.DOWNTREND, Regime.HIGH_VOL_CHAOS}
WARMUP = 210
LONG, SHORT = "long", "short"

# Defaults chosen from development backtests (seeds 1-12) and confirmed on
# held-out data (seeds 13-30): squeeze_breakout lost money on both sets, and on
# the short side only the Donchian breakout showed an edge.
DEFAULT_LONG = ["trend_pullback", "mean_reversion", "donchian_trend", "rsi2_pullback"]
DEFAULT_SHORT = ["donchian_trend"]


def invert(df: pd.DataFrame, k: float) -> pd.DataFrame:
    """Mirror a price series: rallies become sell-offs and vice versa."""
    return pd.DataFrame({"open": k / df["open"], "high": k / df["low"], "low": k / df["high"],
                         "close": k / df["close"], "volume": df["volume"]}, index=df.index)


_SWAPS = {"above": "below", "below": "above", "high": "low", "low": "high", "highs": "lows", "lows": "highs",
          "uptrend": "downtrend", "downtrend": "uptrend", "bullish": "bearish", "bearish": "bullish",
          "oversold": "overbought", "overbought": "oversold", "rising": "falling", "falling": "rising",
          "up": "down", "down": "up", "breakout": "breakdown", "accumulation": "distribution",
          "dipped": "spiked", "dip": "spike", "bounce": "drop", "upper": "lower", "lower": "upper"}
_SWAP_RE = re.compile(r"\b(" + "|".join(sorted(_SWAPS, key=len, reverse=True)) + r")\b")


def mirror_text(text: str) -> str:
    """Translate a reason written for the mirrored chart into real-chart terms."""
    return _SWAP_RE.sub(lambda m: _SWAPS[m.group(1)], text)


def mirror_constant(price: float) -> float:
    """K chosen so mirrored prices have the same order of magnitude."""
    return 10.0 ** (2 * math.floor(math.log10(price)) + 1)


@dataclass
class Position:
    strategy: str
    entry: float          # side-space prices (== real prices for longs)
    qty: float            # real units of the asset
    stop: float
    initial_stop: float
    target: float
    opened_at: str
    side: str = LONG
    k: float = 1.0        # mirror constant (shorts)
    real_entry: float = 0.0
    bars_held: int = 0
    highest: float = 0.0
    entry_fee: float = 0.0
    funding: float = 0.0

    def to_real(self, x: float) -> float:
        return x if self.side == LONG else self.k / x

    @property
    def risk_per_unit(self) -> float:   # side space
        return self.entry - self.initial_stop

    @property
    def real_risk(self) -> float:       # $ at risk on the whole position
        return self.qty * abs(self.to_real(self.initial_stop) - self.real_entry)


@dataclass
class TradeRecord:
    strategy: str
    opened_at: str
    closed_at: str
    entry: float
    exit: float
    qty: float
    pnl: float
    r_multiple: float
    reason: str
    side: str = LONG


@dataclass
class Decision:
    time: str
    price: float
    regime: str
    action: str
    detail: str
    equity: float
    signals: list[str] = field(default_factory=list)


class TradingEngine:
    def __init__(self, broker, risk_cfg: RiskConfig | None = None, htf_factor: int = 4,
                 allow_short: bool = False, strategies: list[str] | None = None,
                 short_strategies: list[str] | None = None):
        self.broker = broker
        self.risk = RiskManager(risk_cfg)
        self.allow_short = allow_short
        def make(names):
            return StrategySelector([c() for c in ALL_STRATEGIES if c.name in names])
        self.selectors = {LONG: make(strategies or DEFAULT_LONG)}
        if allow_short:
            self.selectors[SHORT] = make(short_strategies or DEFAULT_SHORT)
        self.position: Position | None = None
        self.trades: list[TradeRecord] = []
        self.decisions: list[Decision] = []
        self.htf_factor = htf_factor
        self.bar_index = 0
        self.k: float | None = None

    @property
    def selector(self) -> StrategySelector:  # backwards compatible alias
        return self.selectors[LONG]

    # ------------------------------------------------------------------
    @staticmethod
    def prepare(df: pd.DataFrame, htf_factor: int = 4) -> pd.DataFrame:
        out = add_all(df)
        out["htf_bull"] = higher_timeframe_trend(df, htf_factor)
        return out

    def prepare_views(self, df: pd.DataFrame) -> dict[str, pd.DataFrame]:
        views = {LONG: self.prepare(df, self.htf_factor)}
        if self.allow_short:
            if self.k is None:
                self.k = mirror_constant(float(df["close"].iloc[0]))
            views[SHORT] = self.prepare(invert(df, self.k), self.htf_factor)
        return views

    def step(self, views, intrabar: bool = True) -> Decision:
        """Process the last *closed* bar. `views` is the dict from
        prepare_views (or a single prepared DataFrame for long-only)."""
        if isinstance(views, pd.DataFrame):
            views = {LONG: views}
        self.bar_index += 1
        data = views[LONG]
        bar = data.iloc[-1]
        t = str(data.index[-1])
        price = float(bar.close)
        for side, sel in self.selectors.items():
            if side in views:
                sel.update_shadows(views[side].iloc[-1])

        readings = {s: classify(v.iloc[-1], bool(v.iloc[-1].get("htf_bull", True))) for s, v in views.items()}
        regime = readings[LONG].regime

        if self.position and self.risk.cfg.funding_per_bar:
            cost = self.broker.accrue_funding(price, self.risk.cfg.funding_per_bar)
            self.position.funding += cost
        self.risk.on_equity(self.broker.equity(price), t[:10])
        if self.position and self.risk.state.halted:
            msg = self._close(self._side_price(price), t, self.risk.state.halt_reason)
            return self._decide(t, price, regime, "close", msg)

        # 1. Manage the open position ------------------------------------
        if self.position:
            p = self.position
            closed = self._manage_position(views[p.side], readings[p.side], intrabar)
            if closed:
                return self._decide(t, price, regime, "close", closed)
            if self.position:
                return self._decide(t, price, regime, "hold",
                                    f"in {p.side} {p.strategy}; stop {p.to_real(p.stop):.2f} "
                                    f"target {p.to_real(p.target):.2f}")

        equity = self.broker.equity(price)

        # 2. Look for a new trade ----------------------------------------
        ok, why = self.risk.can_trade(equity, self.bar_index)
        if not ok:
            return self._decide(t, price, regime, "stand_aside", why)

        candidates, sig_txt = [], []
        for side, view in views.items():
            rd = readings[side]
            if rd.regime in HOSTILE:
                real = mirror_text(rd.regime.value) if side == SHORT else rd.regime.value
                sig_txt.append(f"{side}s: stand aside (market is in {real.replace('_', ' ')})")
                continue
            best, signals = self.selectors[side].evaluate(view, rd)
            tr = mirror_text if side == SHORT else str
            sig_txt += [f"{side} {s.strategy}: {s.action.value} ({tr('; '.join(s.reasons))})" for s in signals]
            if best:
                candidates.append((best.confidence, side, best))
        if not candidates:
            detail = "no qualifying setup" if any(r.regime not in HOSTILE for r in readings.values()) \
                else f"{regime.value}: cash is a position ({readings[LONG].notes})"
            return self._decide(t, price, regime, "wait", detail, sig_txt)

        _, side, best = max(candidates, key=lambda c: (c[0], c[1] == LONG))
        k = self.k if side == SHORT else 1.0
        real = (lambda x: x) if side == LONG else (lambda x: k / x)
        sizing = self.risk.size(equity, self.broker.cash, price, real(best.stop), real(best.target),
                                best.confidence, side=side)
        if not sizing.approved:
            return self._decide(t, price, regime, "skip", f"{side} {best.strategy} rejected: {sizing.reason}", sig_txt)

        fill = self.broker.open(side, sizing.qty, price)
        entry_side = views[side].close.iloc[-1]
        self.position = Position(best.strategy, float(entry_side), fill.qty, best.stop, best.stop, best.target, t,
                                 side=side, k=k, real_entry=fill.price, highest=float(entry_side),
                                 entry_fee=fill.fee)
        self.broker.place_stop(side, fill.qty, real(best.stop))
        verb = "BUY" if side == LONG else "SHORT"
        detail = (f"{verb} {fill.qty:.6f} @ {fill.price:.2f} via {best.strategy} "
                  f"(conf {best.confidence:.2f}); stop {real(best.stop):.2f}, target {real(best.target):.2f}; "
                  f"{sizing.reason}. Why: {(mirror_text if side == SHORT else str)(', '.join(best.reasons))}")
        return self._decide(t, price, regime, verb.lower(), detail, sig_txt)

    # ------------------------------------------------------------------
    def _side_price(self, real_price: float) -> float:
        p = self.position
        return real_price if p.side == LONG else p.k / real_price

    def _manage_position(self, data: pd.DataFrame, reading, intrabar: bool) -> str | None:
        p = self.position
        bar = data.iloc[-1]
        ts = data.index[-1]
        p.bars_held += 1

        # Stop / target hit inside the bar (stop checked first = conservative)
        if intrabar:
            if bar.low <= p.stop:
                return self._close(min(p.stop, float(bar.open)), ts, "stop loss")
            if bar.high >= p.target:
                return self._close(max(p.target, float(bar.open)), ts, "take profit", exact=True)

        price = float(bar.close)
        p.highest = max(p.highest, float(bar.high))
        r_now = (price - p.entry) / p.risk_per_unit if p.risk_per_unit > 0 else 0

        new_stop = p.stop
        if r_now >= 1.0:   # break-even: a winner never turns into a loser
            new_stop = max(new_stop, p.entry * (1 + 2 * self.risk.cfg.fee_rate))
        if r_now >= 1.5:   # chandelier trailing stop
            new_stop = max(new_stop, p.highest - 2.5 * float(bar.atr))
        if new_stop > p.stop:
            p.stop = new_stop
            self.broker.place_stop(p.side, p.qty, p.to_real(p.stop))

        if reading.regime in HOSTILE and r_now < 1.0:
            return self._close(price, ts, f"regime turned hostile for {p.side}s")
        ex = self.selectors[p.side].by_name(p.strategy).exit(data, p.entry)
        if ex.action == Action.SELL:
            why = "; ".join(ex.reasons)
            return self._close(price, ts, mirror_text(why) if p.side == SHORT else why)
        if p.bars_held > 48 and r_now < 0.5:
            return self._close(price, ts, "time stop - trade not working")
        return None

    def _close(self, side_price: float, ts, reason: str, exact: bool = False) -> str:
        p = self.position
        fill = self.broker.close(p.side, p.qty, p.to_real(side_price), exact=exact)
        if p.side == LONG:
            gross = fill.qty * (fill.price - p.real_entry)
        else:
            gross = fill.qty * (p.real_entry - fill.price)
        pnl = gross - fill.fee - p.entry_fee - p.funding
        r = pnl / p.real_risk if p.real_risk > 0 else 0.0
        self.trades.append(TradeRecord(p.strategy, p.opened_at, str(ts), p.real_entry, fill.price, p.qty,
                                       pnl, r, reason, p.side))
        self.selectors[p.side].record_trade(p.strategy, r)
        self.risk.on_trade_closed(pnl, self.bar_index)
        self.position = None
        return f"CLOSE {p.side} @ {fill.price:.2f} ({reason}); P&L ${pnl:+.2f} ({r:+.2f}R)"

    def _decide(self, t, price, regime, action, detail, signals=None) -> Decision:
        d = Decision(t, price, regime.value, action, detail, round(self.broker.equity(price), 4), signals or [])
        self.decisions.append(d)
        if action in ("buy", "short", "close"):
            log.info("%s | %s | %s", t, regime.value, detail)
        else:
            log.debug("%s | %s | %s: %s", t, regime.value, action, detail)
        return d

    # ---- persistence (paper/live restarts) ----------------------------
    def state(self) -> dict:
        return {
            "position": asdict(self.position) if self.position else None,
            "risk": asdict(self.risk.state) | {"log": []},
            "selectors": {s: sel.to_dict() for s, sel in self.selectors.items()},
            "bar_index": self.bar_index,
            "k": self.k,
            "trades": [asdict(t) for t in self.trades[-200:]],
        }

    def load_state(self, s: dict) -> None:
        if s.get("position"):
            self.position = Position(**s["position"])
        for k, v in s.get("risk", {}).items():
            setattr(self.risk.state, k, v)
        for side, d in s.get("selectors", {"long": s.get("selector", {})}).items():
            if side in self.selectors:
                self.selectors[side].load_dict(d)
        self.bar_index = s.get("bar_index", 0)
        self.k = s.get("k") or self.k
        self.trades = [TradeRecord(**t) for t in s.get("trades", [])]
