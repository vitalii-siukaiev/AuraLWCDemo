# Adaptive Trading Bot ($50 capital-preservation edition)

A crypto trading bot for Bybit (spot, or USDT perpetuals at 1x) that **reads the market
regime first, then picks the strategy that fits it**, and drops strategies that stop
working. It can go long in uptrends and, on perps, short in downtrends. Above all, its
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
   - Regime turned hostile for the position's side (e.g. a downtrend for a long)? Get out.
   - The strategy's own exit (trend broken, failed breakout, mean reached).
   - Time stop: after 48 bars with < 0.5R of progress, free up the capital.
2. **Analyze the market: what regime are we in?** (`regime.py`)

   | Regime | Detected by | Playbook |
   |---|---|---|
   | strong_uptrend / uptrend | ADX, EMA 21/50/200 stack, +DI/−DI, EMA50 slope, 4h trend filter | **Trend pullback**, **Donchian breakout**, **RSI(2) pullback** |
   | range | low ADX, no directional edge | **Mean reversion** |
   | squeeze | Bollinger width in bottom 15% of last 200 bars | wait (squeeze breakout is opt-in) |
   | downtrend | negative trend score | spot: **stay in cash** · perp: **Donchian breakdown short** |
   | high_vol_chaos | ATR% in top 5% and no trend | **Stay in cash** |

3. **Ask the matching strategies for a setup.** Each one scores its own confluence of indicators:
   - `TrendPullback`: pullback to EMA21 in an uptrend + bullish candle + rising MACD histogram + healthy RSI + OBV accumulation. Won't chase or buy overbought.
   - `MeanReversion`: lower Bollinger band + RSI turning up from oversold + stochastic cross + reversal candle. Won't catch a falling knife.
   - `DonchianTrend` (turtle-style): fresh 55-bar high + ADX ≥ 20 + stacked EMAs + volume. Stop 2.5×ATR; the trailing stop rides the trend; exits on a 20-bar low. The best performer in testing, on both longs and shorts.
   - `RSI2Pullback` (Connors-style): above EMA200, RSI(2) < 10, then a bounce candle. High win rate, small wins.
   - `SqueezeBreakout`: recent squeeze + close above the 20-bar Donchian high + volume ≥ 1.5× average. **Off by default** (it lost money in both development and held-out tests); enable with `--strategies`.

   **Shorts (perp only)** use a *mirror view*: the price series is inverted (p → K/p, highs and lows
   swapped), which turns a downtrend into an uptrend. The same long strategies, stops and trailing rules
   then run on the mirrored chart, so long and short logic can't drift apart. By default only
   `DonchianTrend` trades short (the other strategies showed no short-side edge in testing).
4. **Dynamic selection** (`selector.py`). The signal's confidence is multiplied by the strategy's recent
   track record (rolling expectancy in R). A strategy whose expectancy drops below −0.25R
   over ≥5 trades is **benched**. It keeps paper-trading in the background (shadow trades)
   and is reinstated once it starts working again. Signals that weren't chosen are also
   shadow-tracked, so every strategy keeps an up-to-date record.
5. **Risk manager approval** (`risk.py`). The trade is sized or rejected.

## Capital protection rules

| Rule | Default |
|---|---|
| Risk per trade | 1% / 2% / 3% of equity (`--profile conservative / balanced / aggressive`) |
| Risk while in drawdown | halved at 50% of the kill-switch level, quartered at 80% |
| Minimum reward:risk (after fees) | 1.5 |
| Stop can't be so tight that fees exceed | 25% of the risk |
| Expected move must exceed | 4× round-trip fees |
| Minimum confidence | 0.6 |
| Daily loss limit | −3% / −5% / −7% → done for the day |
| Losing streak | 3 losses → 12-bar cooldown |
| **Kill switch** | −10% / −15% / −20% from peak → close everything, stop trading |
| Leverage | **none, ever**. Perp shorts are 1x, with notional capped at equity |
| Perp funding | charged as a cost every bar (0.01% per 8h) |
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

# Long + short on Bybit USDT perpetuals (1x, lower fees: 0.055% taker), 2% risk per trade
python -m trading_bot backtest --market perp --profile balanced --bars 5000

# Paper trading: simulated $50, real live prices. State is saved to bot_state.json,
# so a restart resumes the open position.
python -m trading_bot paper --symbol BTC/USDT --interval 1h

# Live trading on Bybit (real money): pip install ccxt first
export BOT_API_KEY=... BOT_API_SECRET=...      # trade-only key, NO withdrawal permission
python -m trading_bot live --exchange bybit --symbol BTC/USDT --interval 1h --i-understand-the-risk
python -m trading_bot live --market perp --profile balanced --i-understand-the-risk   # perps, long+short
```

Every decision prints the regime, the action and the reasons, e.g.:

```
[2024-05-31 16:00] strong_uptrend BUY  equity $50.12 | BUY 0.000375 @ 16623.51 via trend_pullback
   (conf 1.00); stop 16323.19, target 17345.24; risking $0.13 for R:R 1.94.
   Why: pullback to EMA21 in uptrend, bullish close back above EMA9, MACD histogram rising, ...
```

## Test results (synthetic data, with Bybit fees)

30 random regime-switching datasets of 4,000 hourly bars (about 5.5 months each), $50 start,
Bybit fees ([fee structure](https://www.bybit.com/en/help-center/article/Trading-Fee-Structure):
spot 0.10%; USDT perpetuals 0.055% taker / 0.02% maker), 0.05% slippage, and perp funding charged as a cost.
Strategy choices were made on seeds 1–12 only. **Seeds 13–30 were held out** and are the numbers to trust:

| Configuration (held-out seeds 13–30) | Mean | Median | Worst | Best | Runs in profit | Max DD |
|---|---|---|---|---|---|---|
| Original bot (spot, 3 strategies, 1%) | +0.5% | 0.0% | −6.0% | +12.9% | 50% | 7.5% |
| Spot, new strategies, 1% (default) | +3.4% | +2.1% | −5.9% | +14.8% | 61% | 6.8% |
| Perp long+short, 1% | +3.7% | +1.8% | −7.9% | +24.8% | 61% | 8.8% |
| **Perp long+short, balanced 2%** | **+8.8%** | **+7.1%** | **−12.6%** | +53.5% | 61% | 13.8% |
| Perp long+short, aggressive 3% | +15.3% | +9.7% | −15.0% | +90.2% | 56% | 18.5% |

How to read this:

* **Most of the extra return comes from risking more per trade.** Returns and losses grow together:
  the 3% profile's worst run lost 15%. There is no free lunch.
* **Strategy changes alone** took the median from 0% to about +2% at the same 1% risk.
* **Shorting** mostly helps in bear markets and raises the best case; the median barely moves.
* **Synthetic data is not the real market.** Its trends are built in, which flatters trend-following.
  Treat these numbers as a check that the machinery works, not as a forecast. Run
  `backtest --market perp --profile balanced --bars 5000` on real Bybit history, then paper-trade.

## Project layout

```
trading_bot/
  indicators.py      EMA, RSI, MACD, Bollinger, ATR, ADX/DI, Stochastic, Donchian, OBV
  regime.py          market regime classifier + higher-timeframe trend filter
  strategies/        trend pullback, mean reversion, Donchian trend, RSI(2) pullback, squeeze breakout
  selector.py        dynamic strategy selection, benching, shadow trades
  risk.py            position sizing and all capital-protection rules
  engine.py          per-candle decision cycle (shared by backtest/paper/live), mirror view for shorts
  broker.py          PaperBroker (fees, slippage, shorts, funding) and CCXTBroker (live spot/perp)
  live.py            polling loop, intrabar stop checks, state persistence
  backtest.py        event-driven backtester (no look-ahead)
  data.py            Bybit / Binance klines, CSV loader, synthetic data generator
tests/               pytest suite (no look-ahead, sizing caps, kill switch, ...)
```

All thresholds live in `RiskConfig` (`risk.py`) and in the strategy files, so you can tune
them there.
