"""CLI:  python -m trading_bot {backtest,analyze,paper,live} [options]"""
from __future__ import annotations

import argparse
import logging

import pandas as pd

from .backtest import run_backtest
from .data import drop_open_candle, fetch, load_csv, synthetic
from .engine import DEFAULT_LONG, DEFAULT_SHORT, TradingEngine
from .regime import classify
from .risk import PROFILES, RiskConfig
from .strategies import ALL_STRATEGIES


def _data(args):
    if args.synthetic:
        return synthetic(args.bars, seed=args.seed)
    if args.csv:
        return load_csv(args.csv)
    return drop_open_candle(fetch(args.exchange, args.symbol, args.interval, total=args.bars, market=args.market),
                            args.interval)


def _bar_hours(interval: str) -> float:
    return pd.Timedelta(interval.replace("m", "min") if interval.endswith("m") else interval) / pd.Timedelta("1h")


def _cfg(args) -> RiskConfig:
    return RiskConfig.build(args.profile, args.exchange, args.market, args.fee, args.capital, _bar_hours(args.interval))


def _list(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",")] if s else None


def main() -> None:
    names = ", ".join(c.name for c in ALL_STRATEGIES)
    ap = argparse.ArgumentParser(prog="trading_bot", description="Adaptive multi-strategy crypto bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("backtest", "analyze", "paper", "live"):
        p = sub.add_parser(name)
        p.add_argument("--symbol", default="BTC/USDT")
        p.add_argument("--interval", default="1h")
        p.add_argument("--capital", type=float, default=50.0)
        p.add_argument("--exchange", choices=["bybit", "binance"], default="bybit")
        p.add_argument("--market", choices=["spot", "perp"], default="spot",
                       help="spot = long only; perp = USDT perpetual at 1x, long and short (lower fees)")
        p.add_argument("--profile", choices=list(PROFILES), default="conservative",
                       help="risk per trade 1%% / 2%% / 3%% with kill switch at -10%% / -15%% / -20%%")
        p.add_argument("--fee", type=float, help="override fee per side, e.g. 0.001 = 0.1%%")
        p.add_argument("--strategies", help=f"long strategies (default {','.join(DEFAULT_LONG)}); "
                                            f"available: {names}")
        p.add_argument("--short-strategies", help=f"short strategies on perp (default {','.join(DEFAULT_SHORT)})")
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
    short = args.market == "perp"
    longs, shorts = _list(args.strategies), _list(args.short_strategies)

    if args.cmd == "backtest":
        cfg = _cfg(args)
        res = run_backtest(_data(args), cfg, allow_short=short, strategies=longs, short_strategies=shorts)
        print(f"{args.exchange} {args.market}, profile {args.profile}, fee {cfg.fee_rate:.3%} per side")
        print(res.report())
        for side, sel in res.engine.selectors.items():
            print(f"Strategy health ({side}):", sel.summary())
        for t in res.engine.trades[-10:]:
            print(f"  {t.closed_at[:16]} {t.side:<5} {t.strategy:<17} {t.pnl:+.2f}$ {t.r_multiple:+.2f}R  {t.reason}")
    elif args.cmd == "analyze":
        eng = TradingEngine(None, _cfg(args), allow_short=short, strategies=longs, short_strategies=shorts)
        views = eng.prepare_views(_data(args))
        r = views["long"].iloc[-1]
        print(f"{views['long'].index[-1]}  close {r.close:.2f}")
        print(f"RSI {r.rsi:.1f} | RSI(2) {r.rsi2:.0f} | MACD hist {r.macd_hist:+.2f} | ADX {r.adx:.1f} | "
              f"%B {r.bb_pctb:.2f} | ATR {r.atr_pct:.2%} | stoch {r.stoch_k:.0f}")
        for side, view in views.items():
            row = view.iloc[-1]
            rd = classify(row, bool(row.htf_bull))
            print(f"[{side}] regime as seen by {side}s: {rd.regime.value} (trend score {rd.trend_score:+.2f}) - {rd.notes}")
            best, sigs = eng.selectors[side].evaluate(view, rd)
            for s in sigs:
                print(f"    {s.strategy:<17} {s.action.value:<5} {'; '.join(s.reasons)}")
            print(f"    best {side} setup:", best.strategy if best else "none")
    else:
        from .live import run
        run(args.symbol, args.interval, args.cmd, _cfg(args), args.state, args.poll, args.once, args.exchange,
            market=args.market, strategies=longs, short_strategies=shorts)


if __name__ == "__main__":
    main()
