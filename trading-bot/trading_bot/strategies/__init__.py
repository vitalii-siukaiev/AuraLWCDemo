from .base import Action, Signal, Strategy
from .breakout import SqueezeBreakout
from .donchian import DonchianTrend
from .mean_reversion import MeanReversion
from .rsi2 import RSI2Pullback
from .trend import TrendPullback

ALL_STRATEGIES: list[type[Strategy]] = [TrendPullback, MeanReversion, SqueezeBreakout, DonchianTrend, RSI2Pullback]

__all__ = ["Action", "Signal", "Strategy", "TrendPullback", "MeanReversion", "SqueezeBreakout", "DonchianTrend", "RSI2Pullback",
           "ALL_STRATEGIES"]
