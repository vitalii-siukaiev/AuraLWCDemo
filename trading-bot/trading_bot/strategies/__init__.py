from .base import Action, Signal, Strategy
from .breakout import SqueezeBreakout
from .mean_reversion import MeanReversion
from .trend import TrendPullback

ALL_STRATEGIES: list[type[Strategy]] = [TrendPullback, MeanReversion, SqueezeBreakout]

__all__ = ["Action", "Signal", "Strategy", "TrendPullback", "MeanReversion", "SqueezeBreakout", "ALL_STRATEGIES"]
