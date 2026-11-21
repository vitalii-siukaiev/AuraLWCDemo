"""Risk management - the part that actually protects the $50.

No indicator can guarantee the next move. What a professional controls is
how much they lose when wrong. Rules enforced here:

* Risk a fixed small fraction of equity per trade (default 1% = $0.50).
* Risk shrinks automatically when in drawdown.
* Every trade needs a stop, and reward:risk >= 1.5 *after* fees.
* The expected move must dwarf trading costs (small accounts die by fees).
* Daily loss limit, consecutive-loss cooldown and a hard kill switch.
* Spot only, no leverage, no shorting: worst case is bounded.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Base (non-VIP) spot fees per side. Bybit: help-center "Trading Fee Structure", VIP 0 spot
# 0.10% maker / 0.10% taker. Some regions differ - check "My Fee Rate" in your account.
EXCHANGE_FEES = {"binance": 0.001, "bybit": 0.001}


@dataclass
class RiskConfig:
    starting_capital: float = 50.0
    risk_per_trade: float = 0.01          # fraction of equity risked per trade
    max_position_frac: float = 0.95       # never put more than this in one position
    min_reward_risk: float = 1.5
    fee_rate: float = 0.001               # 0.1% per side (Binance spot taker)
    slippage: float = 0.0005
    min_notional: float = 5.0             # exchange minimum order value
    min_confidence: float = 0.6
    daily_loss_limit: float = 0.03        # stop for the day after -3%
    max_drawdown: float = 0.10            # kill switch: halt at -10% from peak
    max_consecutive_losses: int = 3
    cooldown_bars: int = 12
    cost_multiple: float = 4.0            # target move must be >= 4x round-trip costs
    max_cost_of_risk: float = 0.25        # fees+slippage may eat at most 25% of the risk (1R)


@dataclass
class RiskState:
    peak_equity: float
    day: str = ""
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    cooldown_until_bar: int = -1
    halted: bool = False
    halt_reason: str = ""
    log: list[str] = field(default_factory=list)


@dataclass
class SizingDecision:
    approved: bool
    qty: float = 0.0
    notional: float = 0.0
    risk_amount: float = 0.0
    reason: str = ""


class RiskManager:
    def __init__(self, cfg: RiskConfig | None = None):
        self.cfg = cfg or RiskConfig()
        self.state = RiskState(peak_equity=self.cfg.starting_capital,
                               day_start_equity=self.cfg.starting_capital)

    @property
    def round_trip_cost(self) -> float:
        return 2 * (self.cfg.fee_rate + self.cfg.slippage)

    def on_equity(self, equity: float, day: str) -> None:
        s = self.state
        if day != s.day:
            s.day, s.day_start_equity = day, equity
        s.peak_equity = max(s.peak_equity, equity)
        dd = 1 - equity / s.peak_equity
        if dd >= self.cfg.max_drawdown and not s.halted:
            s.halted, s.halt_reason = True, f"max drawdown {dd:.1%} reached - trading halted"

    def on_trade_closed(self, pnl: float, bar_index: int) -> None:
        s = self.state
        if pnl < 0:
            s.consecutive_losses += 1
            if s.consecutive_losses >= self.cfg.max_consecutive_losses:
                s.cooldown_until_bar = bar_index + self.cfg.cooldown_bars
                s.consecutive_losses = 0
        else:
            s.consecutive_losses = 0

    def current_risk_fraction(self, equity: float) -> float:
        dd = 1 - equity / self.state.peak_equity
        frac = self.cfg.risk_per_trade
        if dd > 0.05:
            frac *= 0.5   # trade smaller while digging out of a hole
        if dd > 0.08:
            frac *= 0.5
        return frac

    def can_trade(self, equity: float, bar_index: int) -> tuple[bool, str]:
        s = self.state
        if s.halted:
            return False, s.halt_reason
        if s.day_start_equity and equity / s.day_start_equity - 1 <= -self.cfg.daily_loss_limit:
            return False, "daily loss limit hit"
        if bar_index < s.cooldown_until_bar:
            return False, f"cooldown after losing streak ({s.cooldown_until_bar - bar_index} bars left)"
        return True, ""

    def size(self, equity: float, cash: float, price: float, stop: float, target: float,
             confidence: float) -> SizingDecision:
        c = self.cfg
        if confidence < c.min_confidence:
            return SizingDecision(False, reason=f"confidence {confidence:.2f} < {c.min_confidence}")
        if stop >= price:
            return SizingDecision(False, reason="invalid stop")
        fill = price * (1 + c.slippage)
        cost_per_unit = fill * c.fee_rate + max(stop, 0) * (c.fee_rate + c.slippage)
        risk_per_unit = fill - stop * (1 - c.slippage) + cost_per_unit
        reward_per_unit = target * (1 - c.slippage - c.fee_rate) - fill * (1 + c.fee_rate)
        if reward_per_unit <= 0:
            return SizingDecision(False, reason="target doesn't cover costs")
        rr = reward_per_unit / risk_per_unit
        if rr < c.min_reward_risk:
            return SizingDecision(False, reason=f"reward:risk {rr:.2f} < {c.min_reward_risk}")
        stop_dist = 1 - stop / price
        if self.round_trip_cost > c.max_cost_of_risk * stop_dist:
            return SizingDecision(False, reason=f"stop {stop_dist:.2%} too tight - fees would eat >"
                                                f"{c.max_cost_of_risk:.0%} of the risk")
        move = target / price - 1
        if move < c.cost_multiple * self.round_trip_cost:
            return SizingDecision(False, reason=f"expected move {move:.2%} too small vs fees")

        risk_amount = equity * self.current_risk_fraction(equity)
        qty = risk_amount / risk_per_unit
        notional = qty * fill
        max_notional = min(cash, equity) * c.max_position_frac / (1 + c.fee_rate)
        if notional > max_notional:
            notional = max_notional
            qty = notional / fill
        if notional < c.min_notional:
            return SizingDecision(False, reason=f"position ${notional:.2f} below exchange minimum")
        return SizingDecision(True, qty, notional, qty * risk_per_unit,
                              f"risking ${qty * risk_per_unit:.2f} for R:R {rr:.2f}")
