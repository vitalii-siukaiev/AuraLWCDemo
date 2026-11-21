import logging

import numpy as np
import pytest

from trading_bot.backtest import run_backtest
from trading_bot.broker import PaperBroker
from trading_bot.data import synthetic
from trading_bot.engine import WARMUP, TradingEngine
from trading_bot.indicators import add_all
from trading_bot.risk import RiskConfig, RiskManager

logging.disable(logging.CRITICAL)


@pytest.fixture(scope="module")
def df():
    return synthetic(1500, seed=3)


def test_indicators_bounded(df):
    d = add_all(df).dropna()
    assert d.rsi.between(0, 100).all()
    assert d.stoch_k.between(0, 100).all()
    assert (d.atr > 0).all()
    assert (d.adx >= 0).all()


def test_indicators_no_lookahead(df):
    """Values at bar i must not change when future bars are appended."""
    full = add_all(df)
    part = add_all(df.iloc[:1000])
    cols = ["ema21", "rsi", "macd_hist", "atr", "adx", "bb_pctb", "dc_up"]
    np.testing.assert_allclose(full[cols].iloc[999].values, part[cols].iloc[999].values, rtol=1e-9)


def test_risk_per_trade_capped():
    rm = RiskManager(RiskConfig())
    d = rm.size(equity=50, cash=50, price=100, stop=97, target=106, confidence=0.8)
    assert d.approved
    assert d.risk_amount <= 50 * 0.01 + 1e-9
    assert d.notional <= 50


def test_rejects_bad_trades():
    rm = RiskManager(RiskConfig())
    assert not rm.size(50, 50, 100, 97, 101, 0.9).approved      # poor reward:risk
    assert not rm.size(50, 50, 100, 97, 106, 0.3).approved      # low confidence
    assert not rm.size(50, 50, 100, 99.7, 100.9, 0.9).approved  # stop too tight vs fees
    assert not rm.size(3, 3, 100, 97, 106, 0.9).approved        # below exchange minimum


def test_kill_switch_and_daily_limit():
    rm = RiskManager(RiskConfig())
    rm.on_equity(50, "2025-01-01")
    rm.on_equity(48.4, "2025-01-01")
    ok, why = rm.can_trade(48.4, 1)
    assert not ok and "daily" in why
    rm.on_equity(44.9, "2025-01-02")
    assert rm.state.halted


def test_paper_broker_fees():
    b = PaperBroker(50, fee_rate=0.001, slippage=0)
    b.buy(0.1, 100)
    b.sell(0.1, 100)
    assert b.cash == pytest.approx(50 - 0.02)


def test_backtest_protects_capital():
    for seed in (1, 2, 3):
        res = run_backtest(synthetic(2500, seed=seed))
        assert res.max_drawdown <= 0.105
        assert res.end_equity > 50 * 0.89


def test_state_roundtrip(df):
    eng = TradingEngine(PaperBroker(50))
    data = TradingEngine.prepare(df)
    for i in range(WARMUP, 900):
        eng.step(data.iloc[: i + 1])
    s = eng.state()
    eng2 = TradingEngine(PaperBroker(50))
    eng2.load_state(s)
    assert eng2.bar_index == eng.bar_index
    assert (eng2.position is None) == (eng.position is None)
    assert len(eng2.trades) == len(eng.trades)


# ---- short side / perps ------------------------------------------------
from trading_bot.engine import invert, mirror_constant, mirror_text  # noqa: E402


def test_paper_short_pnl():
    b = PaperBroker(50, fee_rate=0.0, slippage=0)
    b.open("short", 0.1, 100)
    assert b.equity(90) == pytest.approx(51.0)     # price fell 10 -> +$1
    b.close("short", 0.1, 90)
    assert b.cash == pytest.approx(51.0) and b.qty == 0


def test_short_sizing_mirrors_long():
    rm = RiskManager(RiskConfig())
    lo = rm.size(50, 50, 100, 97, 106, 0.8, side="long")
    sh = rm.size(50, 50, 100, 103, 94, 0.8, side="short")
    assert lo.approved and sh.approved
    assert sh.risk_amount == pytest.approx(lo.risk_amount, rel=0.02)
    assert not rm.size(50, 50, 100, 97, 94, 0.8, side="short").approved   # stop on wrong side


def test_invert_is_involution(df):
    k = mirror_constant(float(df.close.iloc[0]))
    back = invert(invert(df, k), k)
    np.testing.assert_allclose(back[["open", "high", "low", "close"]].values,
                               df[["open", "high", "low", "close"]].values)
    assert (invert(df, k).high >= invert(df, k).low).all()


def test_mirror_text():
    assert mirror_text("close above 20-bar high in uptrend") == "close below 20-bar low in downtrend"


def test_shorts_profit_in_a_falling_market():
    """A long-only bot sits in cash in a steady decline; shorts should earn."""
    rng = np.random.default_rng(0)
    n = 1500
    close = 30000 * np.exp(np.cumsum(-0.0012 + 0.004 * rng.standard_normal(n)))
    import pandas as pd
    d = pd.DataFrame({"open": np.r_[close[0], close[:-1]], "close": close}, index=pd.date_range("2024", periods=n, freq="1h", tz="UTC"))
    d["high"] = d[["open", "close"]].max(axis=1) * 1.002
    d["low"] = d[["open", "close"]].min(axis=1) * 0.998
    d["volume"] = 100 + 1e5 * (d.close / d.open - 1).abs()
    res = run_backtest(d, RiskConfig.build(market="perp"), allow_short=True)
    assert any(t.side == "short" for t in res.engine.trades)
    assert res.end_equity > 50


def test_perp_state_roundtrip(df):
    eng = TradingEngine(PaperBroker(50), RiskConfig.build(market="perp"), allow_short=True)
    views = eng.prepare_views(df)
    for i in range(WARMUP, 1200):
        eng.step({s: v.iloc[: i + 1] for s, v in views.items()})
    eng2 = TradingEngine(PaperBroker(50), RiskConfig.build(market="perp"), allow_short=True)
    eng2.load_state(eng.state())
    assert eng2.k == eng.k and set(eng2.selectors) == {"long", "short"}
