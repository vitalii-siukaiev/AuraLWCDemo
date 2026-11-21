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
