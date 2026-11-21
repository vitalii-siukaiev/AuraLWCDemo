"""Market regime detection.

A pro trader's first question isn't "buy or sell?" but "what kind of market is
this?". Trend-following loses money in chop; mean-reversion gets run over in
trends. We classify every bar into one regime and let the strategy selector
pick the playbook that fits it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Regime(str, Enum):
    STRONG_UPTREND = "strong_uptrend"
    UPTREND = "uptrend"
    RANGE = "range"
    SQUEEZE = "squeeze"          # low volatility compression -> breakout pending
    DOWNTREND = "downtrend"
    HIGH_VOL_CHAOS = "high_vol_chaos"  # volatility spike, no clear direction


@dataclass
class RegimeReading:
    regime: Regime
    trend_score: float      # -1 (strong down) .. +1 (strong up)
    volatility_rank: float  # 0..1 percentile of ATR%
    htf_bullish: bool       # higher-timeframe trend filter
    notes: str


def higher_timeframe_trend(df: pd.DataFrame, factor: int = 4) -> pd.Series:
    """Resample to a higher timeframe (e.g. 1h -> 4h) and return a boolean
    Series (aligned to df) that is True when the HTF close is above its EMA50
    and EMA21 > EMA50. Uses only completed HTF bars (no look-ahead)."""
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < factor * 60:
        return pd.Series(True, index=df.index)
    step = df.index.to_series().diff().median()
    rule = step * factor
    htf = df["close"].resample(rule, label="right", closed="right").last().dropna()
    e21 = htf.ewm(span=21, adjust=False, min_periods=21).mean()
    e50 = htf.ewm(span=50, adjust=False, min_periods=50).mean()
    bull = ((htf > e50) & (e21 > e50)).astype(float)
    # Labelled at the right edge => value known once that HTF bar has closed.
    aligned = bull.reindex(df.index, method="ffill")
    return aligned.fillna(0.0).astype(bool)


def classify(row: pd.Series, htf_bullish: bool = True) -> RegimeReading:
    adx = row.get("adx", float("nan"))
    vol_rank = row.get("atr_rank", 0.5)
    bbw_rank = row.get("bbw_rank", 0.5)
    vol_rank = 0.5 if pd.isna(vol_rank) else float(vol_rank)
    bbw_rank = 0.5 if pd.isna(bbw_rank) else float(bbw_rank)

    close, e21, e50, e200 = row["close"], row["ema21"], row["ema50"], row["ema200"]
    slope = row.get("ema50_slope", 0.0) or 0.0

    # Trend score: blend of EMA stack, DI balance and EMA50 slope.
    stack = 0.0
    stack += 0.25 if close > e21 else -0.25
    stack += 0.25 if e21 > e50 else -0.25
    if not pd.isna(e200):
        stack += 0.25 if e50 > e200 else -0.25
    di = row.get("pdi", 0) - row.get("mdi", 0)
    di_term = max(-0.25, min(0.25, di / 100))
    trend_score = max(-1.0, min(1.0, stack + di_term + max(-0.25, min(0.25, slope * 10))))

    if vol_rank > 0.95 and (pd.isna(adx) or adx < 25):
        reg, note = Regime.HIGH_VOL_CHAOS, "volatility spike without trend"
    elif not pd.isna(adx) and adx >= 25 and trend_score >= 0.6 and htf_bullish:
        reg, note = Regime.STRONG_UPTREND, f"ADX {adx:.0f}, EMAs stacked up"
    elif trend_score <= -0.4:
        reg, note = Regime.DOWNTREND, f"trend score {trend_score:.2f}"
    elif not pd.isna(adx) and adx >= 20 and trend_score >= 0.35:
        reg, note = Regime.UPTREND, f"ADX {adx:.0f}, moderate up-bias"
    elif bbw_rank < 0.15:
        reg, note = Regime.SQUEEZE, f"band width in bottom {bbw_rank:.0%}"
    else:
        reg, note = Regime.RANGE, f"ADX {adx:.0f}, no directional edge"
    return RegimeReading(reg, trend_score, vol_rank, htf_bullish, note)
