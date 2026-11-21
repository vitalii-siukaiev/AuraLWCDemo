"""Event-driven backtest: replays bars one by one through the same engine the
live bot uses, so there is no look-ahead and no separate 'backtest logic'."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .broker import PaperBroker
from .engine import LONG, WARMUP, TradingEngine
from .risk import RiskConfig


@dataclass
class BacktestResult:
    start_equity: float
    end_equity: float
    buy_hold_equity: float
    max_drawdown: float
    trades: int
    win_rate: float
    profit_factor: float
    avg_r: float
    by_strategy: dict
    regime_mix: dict
    equity_curve: pd.Series
    engine: TradingEngine

    def report(self) -> str:
        lines = [
            "=" * 60,
            f"Start equity      ${self.start_equity:,.2f}",
            f"End equity        ${self.end_equity:,.2f}  ({self.end_equity / self.start_equity - 1:+.2%})",
            f"Buy & hold        ${self.buy_hold_equity:,.2f}  ({self.buy_hold_equity / self.start_equity - 1:+.2%})",
            f"Max drawdown      {self.max_drawdown:.2%}",
            f"Trades            {self.trades}   win rate {self.win_rate:.0%}   "
            f"profit factor {self.profit_factor:.2f}   avg {self.avg_r:+.2f}R",
            "-" * 60, "By strategy:",
        ]
        for k, v in self.by_strategy.items():
            lines.append(f"  {k:<24} trades {v['trades']:>3}  P&L ${v['pnl']:+7.2f}  avg {v['avg_r']:+.2f}R")
        lines.append("Regime mix: " + ", ".join(f"{k} {v:.0%}" for k, v in self.regime_mix.items()))
        if self.engine.risk.state.halted:
            lines.append(f"KILL SWITCH: {self.engine.risk.state.halt_reason}")
        lines.append("=" * 60)
        return "\n".join(lines)


def run_backtest(df: pd.DataFrame, cfg: RiskConfig | None = None, htf_factor: int = 4,
                 allow_short: bool = False, strategies: list[str] | None = None,
                 short_strategies: list[str] | None = None) -> BacktestResult:
    cfg = cfg or RiskConfig()
    broker = PaperBroker(cfg.starting_capital, cfg.fee_rate, cfg.slippage)
    eng = TradingEngine(broker, cfg, htf_factor=htf_factor, allow_short=allow_short, strategies=strategies,
                        short_strategies=short_strategies)
    views = eng.prepare_views(df)
    data = views[LONG]

    equity = []
    for i in range(WARMUP, len(data)):
        lo = max(0, i - 300)
        d = eng.step({s: v.iloc[lo: i + 1] for s, v in views.items()})
        equity.append(d.equity)
    if eng.position:  # close at the end for clean accounting
        last = float(data.close.iloc[-1])
        eng._close(eng._side_price(last), data.index[-1], "end of backtest")
        equity[-1] = broker.equity(last)

    curve = pd.Series(equity, index=data.index[WARMUP:])
    dd = float((1 - curve / curve.cummax()).max()) if len(curve) else 0.0
    pnls = np.array([t.pnl for t in eng.trades])
    wins, losses = pnls[pnls > 0].sum(), -pnls[pnls < 0].sum()
    by: dict = {}
    for t in eng.trades:
        b = by.setdefault(f"{t.side} {t.strategy}", {"trades": 0, "pnl": 0.0, "rs": []})
        b["trades"] += 1
        b["pnl"] += t.pnl
        b["rs"].append(t.r_multiple)
    for b in by.values():
        b["avg_r"] = float(np.mean(b.pop("rs")))
    regimes = Counter(d.regime for d in eng.decisions)
    total = sum(regimes.values()) or 1
    bh = cfg.starting_capital * float(data.close.iloc[-1] / data.close.iloc[WARMUP])
    return BacktestResult(
        cfg.starting_capital, float(curve.iloc[-1]) if len(curve) else cfg.starting_capital, bh, dd,
        len(pnls), float((pnls > 0).mean()) if len(pnls) else 0.0,
        float(wins / losses) if losses else float("inf"),
        float(np.mean([t.r_multiple for t in eng.trades])) if eng.trades else 0.0,
        by, {k: v / total for k, v in regimes.most_common()}, curve, eng,
    )
