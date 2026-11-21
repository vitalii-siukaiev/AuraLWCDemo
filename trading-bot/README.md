# Adaptive Trading Bot ($50 capital-preservation edition)

A crypto spot trading bot that **reads the market regime first, then picks the
strategy that fits it**, and drops strategies that stop working. Above all, its
job is to **not lose the $50**.

> ⚠️ **No bot can guarantee profit or predict the market.** This one is built
> so that when it's wrong (and it will often be wrong) the losses stay small.
> Run it in `paper` mode for weeks before risking real money, and only trade
> money you can afford to lose.

## How it thinks (the "pro trader" loop)

Every closed candle (default 1h, BTC/USDT):

1. **Check the open position first.**
   - Stop-loss / take-profit hit? Exit.
   - At +1R, move the stop to break-even (a winner never turns into a loser).
   - At +1.5R, trail the stop at 2.5×ATR below the high (chandelier stop).
   - Regime turned hostile (downtrend / volatility chaos)? Get out.
   - The strategy's own exit (trend broken, failed breakout, mean reached).
   - Time stop: after 48 bars with < 0.5R of progress, free up the capital.
2. **Analyze the market: what regime are we in?** (`regime.py`)

   | Regime | Detected by | Playbook |
   |---|---|---|
   | strong_uptrend / uptrend | ADX, EMA 21/50/200 stack, +DI/−DI, EMA50 slope, 4h trend filter | **Trend pullback** |
   | range | low ADX, no directional edge | **Mean reversion** (+ breakout watch) |
   | squeeze | Bollinger width in bottom 15% of last 200 bars | **Squeeze breakout** |
   | downtrend | negative trend score | **Stay in cash** |
   | high_vol_chaos | ATR% in top 5% and no trend | **Stay in cash** |

3. **Ask the matching strategies for a setup.** Each one scores its own confluence of indicators:
   - `TrendPullback`: pullback to EMA21 in an uptrend + bullish candle + rising MACD histogram + healthy RSI + OBV accumulation. Won't chase or buy overbought.
   - `MeanReversion`: lower Bollinger band + RSI turning up from oversold + stochastic cross + reversal candle. Won't catch a falling knife.
   - `SqueezeBreakout`: recent squeeze + close above the 20-bar Donchian high + volume ≥ 1.5× average. Ignores breakouts without volume.
4. **Dynamic selection** (`selector.py`). The signal's confidence is multiplied by the strategy's recent
   track record (rolling expectancy in R). A strategy whose expectancy drops below −0.25R
   over ≥5 trades is **benched**. It keeps paper-trading in the background (shadow trades)
   and is reinstated once it starts working again. Signals that weren't chosen are also
   shadow-tracked, so every strategy keeps an up-to-date record.
5. **Risk manager approval** (`risk.py`). The trade is sized or rejected.

## Capital protection rules

| Rule | Default |
|---|---|
| Risk per trade | 1% of equity (≈ $0.50 on $50) |
| Risk while in drawdown | halved after −5%, quartered after −8% |
| Minimum reward:risk (after fees) | 1.5 |
| Stop can't be so tight that fees exceed | 25% of the risk |
| Expected move must exceed | 4× round-trip fees |
| Minimum confidence | 0.6 |
| Daily loss limit | −3% → done for the day |
| Losing streak | 3 losses → 12-bar cooldown |
| **Kill switch** | −10% from peak → close everything, stop trading |
| Leverage / shorting | none (spot long only, worst case is bounded) |
| Exchange-side stop order | placed after every live entry, so you're protected even if the bot crashes |

## Usage

```bash
cd trading-bot
pip install -r requirements.txt

# What does the market look like right now?
python -m trading_bot analyze --symbol BTC/USDT --interval 1h

# Backtest on real Bybit history with Bybit spot fees (default exchange; public data, no key)
python -m trading_bot backtest --exchange bybit --symbol BTC/USDT --interval 1h --bars 5000
python -m trading_bot backtest --exchange bybit --symbol ETH/USDT --interval 4h --bars 3000
# Check your own fee tier in Bybit ("My Fee Rate") and pass it if different, e.g. 0.15%:
python -m trading_bot backtest --exchange bybit --fee 0.0015 --bars 5000

# Offline backtest on synthetic regime-switching data
python -m trading_bot backtest --synthetic --seed 7

# Paper trading: simulated $50, real live prices. State is saved to bot_state.json,
# so a restart resumes the open position.
python -m trading_bot paper --symbol BTC/USDT --interval 1h

# Live trading on Bybit (real money): pip install ccxt first
export BOT_API_KEY=... BOT_API_SECRET=...      # trade-only key, NO withdrawal permission
python -m trading_bot live --exchange bybit --symbol BTC/USDT --interval 1h --i-understand-the-risk
```

Every decision prints the regime, the action and the reasons, e.g.:

```
[2024-05-31 16:00] strong_uptrend BUY  equity $50.12 | BUY 0.000375 @ 16623.51 via trend_pullback
   (conf 1.00); stop 16323.19, target 17345.24; risking $0.13 for R:R 1.94.
   Why: pullback to EMA21 in uptrend, bullish close back above EMA9, MACD histogram rising, ...
```

## Test results (synthetic data)

30 random regime-switching datasets of 4,000 hourly bars. The first 12 were used during
development; the last 18 were held out and never tuned on:

| | Bot mean | Bot worst | Bot max drawdown | Buy & hold mean | Buy & hold worst |
|---|---|---|---|---|---|
| Dev seeds 1–12 | +6.1% | −1.9% | 8.1% | +55% | −68% |
| Held-out 13–30 | +0.6% | −6.4% | 7.2% | +22% | −80% |

Read this honestly: on random data the bot is roughly **breakeven**, because random data has no
edge to find. What the test shows is that the **downside is capped** (worst case ≈ −6% to −10%, versus
−80% for holding). The bot gives up a lot of upside in strong bull runs in exchange for
that protection. Backtest on real data (`backtest --symbol ...`) before trusting it.

## Project layout

```
trading_bot/
  indicators.py      EMA, RSI, MACD, Bollinger, ATR, ADX/DI, Stochastic, Donchian, OBV
  regime.py          market regime classifier + higher-timeframe trend filter
  strategies/        trend pullback, mean reversion, squeeze breakout
  selector.py        dynamic strategy selection, benching, shadow trades
  risk.py            position sizing and all capital-protection rules
  engine.py          per-candle decision cycle (shared by backtest/paper/live)
  broker.py          PaperBroker (fees + slippage) and CCXTBroker (live)
  live.py            polling loop, intrabar stop checks, state persistence
  backtest.py        event-driven backtester (no look-ahead)
  data.py            Binance klines, CSV loader, synthetic data generator
tests/               pytest suite (no look-ahead, sizing caps, kill switch, ...)
```

All thresholds live in `RiskConfig` (`risk.py`) and in the strategy files, so you can tune
them there.
