"""Paper / live runner. Polls the exchange, runs one engine step per closed
candle, checks the open position's stop between candles, and persists state
to disk so a restart picks up the open position where it left off."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests

from .broker import CCXTBroker, PaperBroker
from .data import drop_open_candle, fetch
from .engine import WARMUP, Position, TradingEngine
from .risk import RiskConfig

log = logging.getLogger("bot")


def last_price(symbol: str, exchange: str = "bybit") -> float:
    sym = symbol.replace("/", "")
    if exchange == "bybit":
        for base in ("https://api.bybit.com", "https://api.bytick.com"):
            try:
                r = requests.get(f"{base}/v5/market/tickers", params={"category": "spot", "symbol": sym}, timeout=10)
                r.raise_for_status()
                return float(r.json()["result"]["list"][0]["lastPrice"])
            except Exception:
                continue
        raise RuntimeError("price unavailable")
    for base in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            r = requests.get(f"{base}/api/v3/ticker/price", params={"symbol": symbol.replace("/", "")}, timeout=10)
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception:
            continue
    raise RuntimeError("price unavailable")


def run(symbol: str = "BTC/USDT", interval: str = "1h", mode: str = "paper", cfg: RiskConfig | None = None,
        state_file: str = "bot_state.json", poll_seconds: int = 60, once: bool = False,
        exchange: str = "bybit") -> None:
    cfg = cfg or RiskConfig()
    capital = cfg.starting_capital
    path = Path(state_file)
    saved = json.loads(path.read_text()) if path.exists() else {}

    if mode == "live":
        key, secret = os.environ.get("BOT_API_KEY"), os.environ.get("BOT_API_SECRET")
        if not key or not secret:
            raise SystemExit("live mode needs BOT_API_KEY and BOT_API_SECRET environment variables")
        broker = CCXTBroker(exchange, symbol, key, secret, cfg.fee_rate)
    else:
        broker = PaperBroker(saved.get("cash", capital), cfg.fee_rate, cfg.slippage)
        broker.qty = saved.get("qty", 0.0)

    eng = TradingEngine(broker, cfg)
    if saved:
        eng.load_state(saved)
        log.info("resumed state: position=%s", eng.position)
    if mode == "live":
        _reconcile(eng, broker)

    last_bar = saved.get("last_bar")
    while True:
        try:
            df = drop_open_candle(fetch(exchange, symbol, interval, limit=WARMUP + 300), interval)
            data = TradingEngine.prepare(df)
            bar_time = str(data.index[-1])
            if bar_time != last_bar:
                d = eng.step(data)
                last_bar = bar_time
                print(f"[{d.time}] {d.regime:<15} {d.action.upper():<12} equity ${d.equity:.2f} | {d.detail}")
                for s in d.signals:
                    print(f"      - {s}")
            elif eng.position:
                _check_stop_between_bars(eng, symbol, exchange)
            _save(path, eng, broker, last_bar, mode)
        except Exception as e:
            log.exception("cycle failed: %s", e)
        if once:
            return
        time.sleep(poll_seconds)


def _check_stop_between_bars(eng: TradingEngine, symbol: str, exchange: str) -> None:
    p: Position = eng.position
    px = last_price(symbol, exchange)
    if px <= p.stop:
        print(f"stop hit intrabar at {px:.2f} -> {eng._close(px, 'now', 'stop loss (intrabar)')}")
    elif px >= p.target:
        print(f"target hit intrabar at {px:.2f} -> {eng._close(px, 'now', 'take profit (intrabar)')}")


def _reconcile(eng: TradingEngine, broker: CCXTBroker) -> None:
    """Trust the exchange over the state file."""
    held = broker.qty
    if eng.position and held < eng.position.qty * 0.5:
        log.warning("state says position open but exchange balance is %.8f - clearing (stop probably filled)", held)
        eng.position = None
    elif eng.position:
        eng.position.qty = min(eng.position.qty, held)


def _save(path: Path, eng: TradingEngine, broker, last_bar, mode) -> None:
    s = eng.state() | {"last_bar": last_bar, "mode": mode}
    if mode != "live":
        s |= {"cash": broker.cash, "qty": broker.qty}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, default=str))
    tmp.replace(path)
