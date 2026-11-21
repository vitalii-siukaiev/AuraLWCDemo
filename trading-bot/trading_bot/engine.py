"""The trading engine: one decision cycle per closed candle.

Cycle order (same in backtest, paper and live):
  1. Mark-to-market, update risk state (drawdown, daily limit).
  2. If a position is open: check stop / target, update break-even and
     trailing stop, ask the owning strategy for a discretionary exit, and
     exit if the regime turned hostile.
  3. If flat: detect regime, ask fitting strategies for signals, pick the
     best one (dynamic selector), size it through the risk manager, enter.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import pandas as pd

from .indicators import add_all
from .regime import Regime, classify, higher_timeframe_trend
from .risk import RiskConfig, RiskManager
from .selector import StrategySelector
from .strategies import Action

log = logging.getLogger("bot")

HOSTILE = {Regime.DOWNTREND, Regime.HIGH_VOL_CHAOS}
WARMUP = 210


@dataclass
class Position:
    strategy: str
    entry: float
    qty: float
    stop: float
    initial_stop: float
    target: float
    opened_at: str
    bars_held: int = 0
    highest: float = 0.0
    entry_fee: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        return self.entry - self.initial_stop


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
    def __init__(self, broker, risk_cfg: RiskConfig | None = None, selector: StrategySelector | None = None,
                 htf_factor: int = 4):
        self.broker = broker
        self.risk = RiskManager(risk_cfg)
        self.selector = selector or StrategySelector()
        self.position: Position | None = None
        self.trades: list[TradeRecord] = []
        self.decisions: list[Decision] = []
        self.htf_factor = htf_factor
        self.bar_index = 0

    # ------------------------------------------------------------------
    @staticmethod
    def prepare(df: pd.DataFrame, htf_factor: int = 4) -> pd.DataFrame:
        out = add_all(df)
        out["htf_bull"] = higher_timeframe_trend(df, htf_factor)
        return out

    def step(self, data: pd.DataFrame, intrabar: bool = True) -> Decision:
        """Process the last *closed* bar of `data` (already prepared)."""
        self.bar_index += 1
        bar = data.iloc[-1]
        t = str(data.index[-1])
        price = float(bar.close)
        self.selector.update_shadows(bar)

        reading = classify(bar, bool(bar.get("htf_bull", True)))
        self.risk.on_equity(self.broker.equity(price), t[:10])
        if self.position and self.risk.state.halted:
            msg = self._close(price, data.index[-1], self.risk.state.halt_reason)
            return self._decide(t, price, reading.regime, "sell", msg)

        # 1. Manage the open position ------------------------------------
        if self.position:
            closed = self._manage_position(data, reading, intrabar)
            if closed:
                return self._decide(t, price, reading.regime, "sell", closed)
            if self.position:
                p = self.position
                return self._decide(t, price, reading.regime, "hold",
                                    f"in {p.strategy} position; stop {p.stop:.2f} target {p.target:.2f}")

        equity = self.broker.equity(price)

        # 2. Look for a new trade ----------------------------------------
        ok, why = self.risk.can_trade(equity, self.bar_index)
        if not ok:
            return self._decide(t, price, reading.regime, "stand_aside", why)
        if reading.regime in HOSTILE:
            return self._decide(t, price, reading.regime, "stand_aside",
                                f"{reading.regime.value}: cash is a position ({reading.notes})")

        best, signals = self.selector.evaluate(data, reading)
        sig_txt = [f"{s.strategy}: {s.action.value} ({'; '.join(s.reasons)})" for s in signals]
        if best is None:
            return self._decide(t, price, reading.regime, "wait", "no qualifying setup", sig_txt)

        sizing = self.risk.size(equity, self.broker.cash, price, best.stop, best.target, best.confidence)
        if not sizing.approved:
            return self._decide(t, price, reading.regime, "skip", f"{best.strategy} rejected: {sizing.reason}", sig_txt)

        fill = self.broker.buy(sizing.qty, price)
        self.position = Position(best.strategy, fill.price, fill.qty, best.stop, best.stop, best.target,
                                 t, highest=fill.price, entry_fee=fill.fee)
        self.broker.place_stop(fill.qty, best.stop)
        detail = (f"BUY {fill.qty:.6f} @ {fill.price:.2f} via {best.strategy} "
                  f"(conf {best.confidence:.2f}); stop {best.stop:.2f}, target {best.target:.2f}; "
                  f"{sizing.reason}. Why: {', '.join(best.reasons)}")
        return self._decide(t, price, reading.regime, "buy", detail, sig_txt)

    # ------------------------------------------------------------------
    def _manage_position(self, data: pd.DataFrame, reading, intrabar: bool) -> str | None:
        p = self.position
        bar = data.iloc[-1]
        p.bars_held += 1

        # Stop / target hit inside the bar (stop checked first = conservative)
        if intrabar:
            if bar.low <= p.stop:
                fill_px = min(p.stop, float(bar.open))  # gap-through fills worse
                return self._close(fill_px, data.index[-1], "stop loss", exact=False)
            if bar.high >= p.target:
                # A gap above the target fills at the (better) open.
                return self._close(max(p.target, float(bar.open)), data.index[-1], "take profit", exact=True)

        price = float(bar.close)
        p.highest = max(p.highest, float(bar.high))
        r_now = (price - p.entry) / p.risk_per_unit if p.risk_per_unit > 0 else 0

        # Break-even once +1R: a winner is never allowed to become a loser.
        new_stop = p.stop
        if r_now >= 1.0:
            new_stop = max(new_stop, p.entry * (1 + 2 * self.risk.cfg.fee_rate))
        # Chandelier trailing stop after +1.5R
        if r_now >= 1.5:
            new_stop = max(new_stop, p.highest - 2.5 * float(bar.atr))
        if new_stop > p.stop:
            p.stop = new_stop
            self.broker.place_stop(p.qty, p.stop)

        if reading.regime in HOSTILE and r_now < 1.0:
            return self._close(price, data.index[-1], f"regime turned {reading.regime.value}")
        ex = self.selector.by_name(p.strategy).exit(data, p.entry)
        if ex.action == Action.SELL:
            return self._close(price, data.index[-1], "; ".join(ex.reasons))
        if p.bars_held > 48 and r_now < 0.5:
            return self._close(price, data.index[-1], "time stop - trade not working")
        return None

    def _close(self, price: float, ts, reason: str, exact: bool = False) -> str:
        p = self.position
        fill = self.broker.sell(p.qty, price, exact=exact)
        pnl = fill.qty * fill.price - fill.fee - p.qty * p.entry - p.entry_fee
        r = pnl / (p.qty * p.risk_per_unit) if p.risk_per_unit > 0 else 0.0
        self.trades.append(TradeRecord(p.strategy, p.opened_at, str(ts), p.entry, fill.price, p.qty, pnl, r, reason))
        self.selector.record_trade(p.strategy, r)
        self.risk.on_trade_closed(pnl, self.bar_index)
        self.position = None
        return f"SELL @ {fill.price:.2f} ({reason}); P&L ${pnl:+.2f} ({r:+.2f}R)"

    def _decide(self, t, price, regime, action, detail, signals=None) -> Decision:
        d = Decision(t, price, regime.value, action, detail, round(self.broker.equity(price), 4), signals or [])
        self.decisions.append(d)
        if action in ("buy", "sell"):
            log.info("%s | %s | %s", t, regime.value, detail)
        else:
            log.debug("%s | %s | %s: %s", t, regime.value, action, detail)
        return d

    # ---- persistence (paper/live restarts) ----------------------------
    def state(self) -> dict:
        return {
            "position": asdict(self.position) if self.position else None,
            "risk": asdict(self.risk.state) | {"log": []},
            "selector": self.selector.to_dict(),
            "bar_index": self.bar_index,
            "trades": [asdict(t) for t in self.trades[-200:]],
        }

    def load_state(self, s: dict) -> None:
        if s.get("position"):
            self.position = Position(**s["position"])
        for k, v in s.get("risk", {}).items():
            setattr(self.risk.state, k, v)
        self.selector.load_dict(s.get("selector", {}))
        self.bar_index = s.get("bar_index", 0)
        self.trades = [TradeRecord(**t) for t in s.get("trades", [])]
