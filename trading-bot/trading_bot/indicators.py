"""Technical indicators implemented with pandas only (no TA-Lib needed).

Every function takes a DataFrame with columns open/high/low/close/volume
(or a Series) and returns Series aligned to the input index.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0).where(loss.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower)
    return mid, upper, lower, width, pct_b


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14):
    """Returns (adx, +DI, -DI)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), plus_di, minus_di


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3):
    lo = df["low"].rolling(n, min_periods=n).min()
    hi = df["high"].rolling(n, min_periods=n).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return k, k.rolling(d, min_periods=d).mean()


def donchian(df: pd.DataFrame, n: int = 20):
    """Channel of the *previous* n bars (excludes the current bar, so a close
    above `upper` is a genuine breakout)."""
    upper = df["high"].rolling(n, min_periods=n).max().shift(1)
    lower = df["low"].rolling(n, min_periods=n).min().shift(1)
    return upper, lower


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with every indicator the strategies use."""
    out = df.copy()
    c = out["close"]
    out["ema9"], out["ema21"], out["ema50"], out["ema200"] = ema(c, 9), ema(c, 21), ema(c, 50), ema(c, 200)
    out["rsi"] = rsi(c, 14)
    out["macd"], out["macd_sig"], out["macd_hist"] = macd(c)
    out["bb_mid"], out["bb_up"], out["bb_lo"], out["bb_width"], out["bb_pctb"] = bollinger(c)
    out["atr"] = atr(out, 14)
    out["atr_pct"] = out["atr"] / c
    out["adx"], out["pdi"], out["mdi"] = adx(out, 14)
    out["stoch_k"], out["stoch_d"] = stochastic(out)
    out["dc_up"], out["dc_lo"] = donchian(out, 20)
    out["vol_sma"] = sma(out["volume"], 20)
    out["obv"] = obv(out)
    out["obv_ema"] = ema(out["obv"], 20)
    # Rolling percentile of volatility / band width (0..1) for regime detection.
    out["atr_rank"] = out["atr_pct"].rolling(200, min_periods=50).rank(pct=True)
    out["bbw_rank"] = out["bb_width"].rolling(200, min_periods=50).rank(pct=True)
    out["ema50_slope"] = out["ema50"].pct_change(10)
    return out
