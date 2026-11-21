"""Brokers: a paper broker (default) and an optional live ccxt broker."""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Fill:
    side: str
    qty: float
    price: float
    fee: float


class PaperBroker:
    """Simulated spot account with fees and slippage."""

    def __init__(self, cash: float, fee_rate: float = 0.001, slippage: float = 0.0005):
        self.cash = cash
        self.qty = 0.0
        self.fee_rate = fee_rate
        self.slippage = slippage

    def buy(self, qty: float, price: float, exact: bool = False) -> Fill:
        px = price if exact else price * (1 + self.slippage)
        fee = qty * px * self.fee_rate
        cost = qty * px + fee
        if cost > self.cash + 1e-9:
            qty = self.cash / (px * (1 + self.fee_rate))
            fee = qty * px * self.fee_rate
            cost = qty * px + fee
        self.cash -= cost
        self.qty += qty
        return Fill("buy", qty, px, fee)

    def sell(self, qty: float, price: float, exact: bool = False) -> Fill:
        qty = min(qty, self.qty)
        px = price if exact else price * (1 - self.slippage)
        fee = qty * px * self.fee_rate
        self.cash += qty * px - fee
        self.qty -= qty
        if self.qty < 1e-12:
            self.qty = 0.0
        return Fill("sell", qty, px, fee)

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    # Exchange-side protective orders are a no-op on paper: the engine
    # simulates stops from bar highs/lows.
    def place_stop(self, qty: float, stop: float) -> None: ...
    def cancel_stop(self) -> None: ...


class CCXTBroker:
    """Live spot trading through ccxt (e.g. Binance). Requires `pip install ccxt`
    and API keys in BOT_API_KEY / BOT_API_SECRET. Places a real exchange-side
    stop-loss order after every entry so the position is protected even if
    the bot crashes or loses connectivity."""

    def __init__(self, exchange_id: str, symbol: str, api_key: str, secret: str, fee_rate: float = 0.001):
        import ccxt  # noqa: lazy optional dependency

        self.ex = getattr(ccxt, exchange_id)({"apiKey": api_key, "secret": secret, "enableRateLimit": True})
        self.ex.load_markets()
        self.symbol = symbol
        self.base, self.quote = symbol.split("/")
        self.fee_rate = fee_rate
        self.stop_order_id: str | None = None

    @property
    def cash(self) -> float:
        return float(self.ex.fetch_balance()["free"].get(self.quote, 0.0))

    @property
    def qty(self) -> float:
        bal = self.ex.fetch_balance()
        return float(bal["total"].get(self.base, 0.0))

    def _amount(self, qty: float) -> float:
        return float(self.ex.amount_to_precision(self.symbol, qty))

    def buy(self, qty: float, price: float, exact: bool = False) -> Fill:
        o = self.ex.create_order(self.symbol, "market", "buy", self._amount(qty))
        filled = float(o.get("filled") or qty)
        avg = float(o.get("average") or price)
        return Fill("buy", filled, avg, filled * avg * self.fee_rate)

    def sell(self, qty: float, price: float, exact: bool = False) -> Fill:
        self.cancel_stop()
        qty = min(qty, self.qty)
        o = self.ex.create_order(self.symbol, "market", "sell", self._amount(qty))
        filled = float(o.get("filled") or qty)
        avg = float(o.get("average") or price)
        return Fill("sell", filled, avg, filled * avg * self.fee_rate)

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def place_stop(self, qty: float, stop: float) -> None:
        self.cancel_stop()
        try:
            limit = float(self.ex.price_to_precision(self.symbol, stop * 0.995))
            sp = float(self.ex.price_to_precision(self.symbol, stop))
            o = self.ex.create_order(self.symbol, "STOP_LOSS_LIMIT", "sell", self._amount(qty), limit,
                                     {"stopPrice": sp, "timeInForce": "GTC"})
            self.stop_order_id = o["id"]
        except Exception as e:  # exchange may not support this order type
            log.warning("could not place exchange-side stop (%s) - bot will manage stop by polling", e)

    def cancel_stop(self) -> None:
        if self.stop_order_id:
            try:
                self.ex.cancel_order(self.stop_order_id, self.symbol)
            except Exception as e:
                log.warning("cancel stop failed: %s", e)
            self.stop_order_id = None
