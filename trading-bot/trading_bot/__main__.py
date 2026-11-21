"""CLI:  python -m trading_bot {backtest,analyze,paper,live} [options]"""
from __future__ import annotations

import argparse
import logging

from .backtest import run_backtest
from .data import drop_open_candle, fetch_binance, load_csv, synthetic
from .engine import TradingEngine
from .regime import classify
from .risk import RiskConfig
from .selector import StrategySelector


def _data(args):
    if args.synthetic:
        return synthetic(args.bars, seed=args.seed)
    if args.csv:
        return load_csv(args.csv)
    return drop_open_candle(fetch_binance(args.symbol, args.interval, total=args.bars), args.interval)


def main() -> None:
    ap = argparse.ArgumentParser(prog="trading_bot", description="Adaptive multi-strategy crypto bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("backtest", "analyze", "paper", "live"):
        p = sub.add_parser(name)
        p.add_argument("--symbol", default="BTC/USDT")
        p.add_argument("--interval", default="1h")
        p.add_argument("--capital", type=float, default=50.0)
        p.add_argument("-v", "--verbose", action="store_true")
        if name in ("backtest", "analyze"):
            p.add_argument("--bars", type=int, default=3000)
            p.add_argument("--synthetic", action="store_true", help="offline regime-switching test data")
            p.add_argument("--seed", type=int, default=7)
            p.add_argument("--csv", help="OHLCV csv file")
        else:
            p.add_argument("--state", default="bot_state.json")
            p.add_argument("--poll", type=int, default=60)
            p.add_argument("--once", action="store_true", help="run a single cycle and exit")
        if name == "live":
            p.add_argument("--i-understand-the-risk", action="store_true", required=True,
                           help="required: live mode trades real money")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.cmd == "backtest":
        res = run_backtest(_data(args), RiskConfig(starting_capital=args.capital))
        print(res.report())
        print("Strategy health:", res.engine.selector.summary())
        for t in res.engine.trades[-10:]:
            print(f"  {t.closed_at[:16]} {t.strategy:<17} {t.pnl:+.2f}$ {t.r_multiple:+.2f}R  {t.reason}")
    elif args.cmd == "analyze":
        data = TradingEngine.prepare(_data(args))
        r = data.iloc[-1]
        reading = classify(r, bool(r.htf_bull))
        print(f"{data.index[-1]}  close {r.close:.2f}")
        print(f"Regime: {reading.regime.value}  (trend score {reading.trend_score:+.2f}, "
              f"vol rank {reading.volatility_rank:.0%}, HTF bullish {reading.htf_bullish}) - {reading.notes}")
        print(f"RSI {r.rsi:.1f} | MACD hist {r.macd_hist:+.2f} | ADX {r.adx:.1f} | %B {r.bb_pctb:.2f} | "
              f"ATR {r.atr_pct:.2%} | stoch {r.stoch_k:.0f}")
        best, sigs = StrategySelector().evaluate(data, reading)
        for s in sigs:
            print(f"  {s.strategy:<17} {s.action.value:<5} {'; '.join(s.reasons)}")
        print("Best setup:", f"{best.strategy} stop {best.stop:.2f} target {best.target:.2f}" if best else "none - stay in cash")
    else:
        from .live import run
        run(args.symbol, args.interval, args.cmd, args.capital, args.state, args.poll, args.once)


if __name__ == "__main__":
    main()
