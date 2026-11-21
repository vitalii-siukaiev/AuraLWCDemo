"""Market data: Binance public klines (no API key), CSV, or synthetic."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import requests

BINANCE_URLS = ["https://api.binance.com", "https://data-api.binance.vision"]
COLUMNS = ["open", "high", "low", "close", "volume"]


def fetch_binance(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 1000,
                  total: int | None = None) -> pd.DataFrame:
    """Fetch OHLCV candles. `total` > 1000 pages backwards in time."""
    total = total or limit
    rows: list = []
    end_time = None
    last_err = None
    while len(rows) < total:
        params = {"symbol": symbol.replace("/", ""), "interval": interval, "limit": min(1000, total - len(rows))}
        if end_time:
            params["endTime"] = end_time
        for base in BINANCE_URLS:
            try:
                r = requests.get(f"{base}/api/v3/klines", params=params, timeout=15)
                r.raise_for_status()
                batch = r.json()
                break
            except Exception as e:  # try the mirror
                last_err = e
        else:
            raise RuntimeError(f"could not fetch market data: {last_err}")
        if not batch:
            break
        rows = batch + rows
        end_time = batch[0][0] - 1
        if len(batch) < params["limit"]:
            break
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["t", *COLUMNS, "ct", "qv", "n", "tb", "tq", "i"])
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df[COLUMNS].astype(float)
    return df[~df.index.duplicated()].sort_index()


def drop_open_candle(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Binance returns the still-forming candle last; never trade on it."""
    step = pd.Timedelta(interval.replace("m", "min")) if interval.endswith("m") else pd.Timedelta(interval)
    if len(df) and df.index[-1] + step > pd.Timestamp.now(tz="UTC"):
        return df.iloc[:-1]
    return df


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    tcol = next(c for c in df.columns if c.lower() in ("time", "timestamp", "date", "datetime", "open_time"))
    idx = pd.to_datetime(df[tcol], unit="ms" if np.issubdtype(df[tcol].dtype, np.number) else None, utc=True)
    df.columns = [c.lower() for c in df.columns]
    out = df[COLUMNS].astype(float)
    out.index = idx
    return out.sort_index()


def synthetic(n: int = 5000, seed: int = 7, start_price: float = 30000.0, freq: str = "1h") -> pd.DataFrame:
    """Regime-switching random walk (trend up / trend down / range / squeeze /
    volatile) for offline testing. It is NOT a forecast of real markets."""
    rng = np.random.default_rng(seed)
    regimes = [  # (drift per bar, vol per bar, mean-reversion strength)
        (0.0008, 0.006, 0.0),   # uptrend
        (-0.0008, 0.007, 0.0),  # downtrend
        (0.0, 0.005, 0.05),     # range
        (0.0, 0.002, 0.02),     # squeeze
        (0.0, 0.016, 0.0),      # chaos
    ]
    closes, vols = [], []
    price, anchor = start_price, start_price
    i = 0
    while i < n:
        k = rng.integers(len(regimes))
        drift, vol, mr = regimes[k]
        length = int(rng.integers(80, 400))
        anchor = price
        for _ in range(min(length, n - i)):
            ret = drift + vol * rng.standard_t(5) / np.sqrt(5 / 3) - mr * np.log(price / anchor)
            price *= np.exp(ret)
            closes.append(price)
            vols.append(vol)
            i += 1
    closes = np.array(closes)
    vols = np.array(vols)
    opens = np.concatenate([[start_price], closes[:-1]])
    wick = np.abs(rng.normal(0, 1, n)) * vols * closes * 0.6
    highs = np.maximum(opens, closes) + wick
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0, 1, n)) * vols * closes * 0.6
    volume = rng.lognormal(3, 0.4, n) * (1 + 40 * np.abs(closes / opens - 1) / vols.mean())
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx)
