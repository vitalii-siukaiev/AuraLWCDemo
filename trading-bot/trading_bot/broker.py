"""Brokers: a paper broker (default) and live ccxt brokers (spot and perp)."""
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
    """Simulated account. `qty` is signed: positive = long, negative = short.
    Shorts are 1x (no leverage): the risk manager caps short notional at equity.
    equity = cash + qty * price holds for both directions."""

    def __init__(self, cash: float, fee_rate: float = 0.001, slippage: float = 0.0005):
        self.cash = cash
        self.qty = 0.0
        self.fee_rate = fee_rate
        self.slippage = slippage

    def _buy(self, qty: float, price: float, exact: bool, cap_to_cash: bool) -> Fill:
        px = price if exact else price * (1 + self.slippage)
        if cap_to_cash and qty * px * (1 + self.fee_rate) > self.cash + 1e-9:
            qty = max(self.cash, 0) / (px * (1 + self.fee_rate))
        fee = qty * px * self.fee_rate
        self.cash -= qty * px + fee
        self.qty += qty
        return Fill("buy", qty, px, fee)

    def _sell(self, qty: float, price: float, exact: bool) -> Fill:
        px = price if exact else price * (1 - self.slippage)
        fee = qty * px * self.fee_rate
        self.cash += qty * px - fee
        self.qty -= qty
        return Fill("sell", qty, px, fee)

    def open(self, side: str, qty: float, price: float, exact: bool = False) -> Fill:
        return self._buy(qty, price, exact, True) if side == "long" else self._sell(qty, price, exact)

    def close(self, side: str, qty: float, price: float, exact: bool = False) -> Fill:
        if side == "long":
            f = self._sell(min(qty, self.qty), price, exact)
        else:
            f = self._buy(min(qty, -self.qty), price, exact, False)
        if abs(self.qty) < 1e-12:
            self.qty = 0.0
        return f

    # backwards-compatible spot helpers
    def buy(self, qty: float, price: float, exact: bool = False) -> Fill:
        return self.open("long", qty, price, exact)

    def sell(self, qty: float, price: float, exact: bool = False) -> Fill:
        return self.close("long", qty, price, exact)

    def accrue_funding(self, price: float, rate: float) -> float:
        """Perp funding, charged conservatively as a cost to either side."""
        cost = abs(self.qty) * price * rate
        self.cash -= cost
        return cost

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    # Paper stops are simulated by the engine from bar highs/lows.
    def place_stop(self, side: str, qty: float, stop: float) -> None: ...
    def cancel_stop(self) -> None: ...


class CCXTBroker:
    """Live trading through ccxt. `market="spot"` trades spot (long only);
    `market="perp"` trades USDT linear perpetuals at 1x leverage (long and
    short). Places an exchange-side stop after every entry so the position is
    protected even if the bot crashes. Requires `pip install ccxt` and API
    keys in BOT_API_KEY / BOT_API_SECRET."""

    def __init__(self, exchange_id: str, symbol: str, api_key: str, secret: str, fee_rate: float,
                 market: str = "spot"):
        import ccxt  # noqa: lazy optional dependency

        opts = {"defaultType": "swap" if market == "perp" else "spot"}
        self.ex = getattr(ccxt, exchange_id)({"apiKey": api_key, "secret": secret,
                                              "enableRateLimit": True, "options": opts})
        self.ex.load_markets()
        self.market = market
        self.base, self.quote = symbol.split(":")[0].split("/")
        self.symbol = f"{self.base}/{self.quote}:{self.quote}" if market == "perp" else f"{self.base}/{self.quote}"
        self.fee_rate = fee_rate
        self.stop_order_id: str | None = None
        if market == "perp":
            try:
                self.ex.set_leverage(1, self.symbol)
            except Exception as e:  # already 1x, or not allowed
                log.info("set_leverage: %s", e)

    # ---- account -------------------------------------------------------
    @property
    def cash(self) -> float:
        return float(self.ex.fetch_balance()["free"].get(self.quote, 0.0))

    @property
    def qty(self) -> float:
        if self.market == "spot":
            return float(self.ex.fetch_balance()["total"].get(self.base, 0.0))
        for p in self.ex.fetch_positions([self.symbol]):
            c = float(p.get("contracts") or 0) * float(p.get("contractSize") or 1)
            if c:
                return c if p.get("side") == "long" else -c
        return 0.0

    def equity(self, price: float) -> float:
        bal = self.ex.fetch_balance()
        if self.market == "spot":
            return float(bal["total"].get(self.quote, 0.0)) + float(bal["total"].get(self.base, 0.0)) * price
        upnl = sum(float(p.get("unrealizedPnl") or 0) for p in self.ex.fetch_positions([self.symbol]))
        return float(bal["total"].get(self.quote, 0.0)) + upnl

    def accrue_funding(self, price: float, rate: float) -> float:
        return 0.0  # the exchange charges real funding to the wallet

    # ---- orders --------------------------------------------------------
    def _amount(self, qty: float) -> float:
        return float(self.ex.amount_to_precision(self.symbol, qty))

    def _market(self, side: str, qty: float, price: float, reduce: bool) -> Fill:
        params = {"reduceOnly": True} if reduce and self.market == "perp" else {}
        o = self.ex.create_order(self.symbol, "market", side, self._amount(qty), None, params)
        filled = float(o.get("filled") or qty)
        avg = float(o.get("average") or price)
        return Fill(side, filled, avg, filled * avg * self.fee_rate)

    def open(self, side: str, qty: float, price: float, exact: bool = False) -> Fill:
        if side == "short" and self.market != "perp":
            raise ValueError("shorting needs market='perp'")
        return self._market("buy" if side == "long" else "sell", qty, price, reduce=False)

    def close(self, side: str, qty: float, price: float, exact: bool = False) -> Fill:
        self.cancel_stop()
        qty = min(qty, abs(self.qty))
        return self._market("sell" if side == "long" else "buy", qty, price, reduce=True)

    def place_stop(self, side: str, qty: float, stop: float) -> None:
        self.cancel_stop()
        exit_side = "sell" if side == "long" else "buy"
        try:
            sp = float(self.ex.price_to_precision(self.symbol, stop))
            if self.market == "perp":
                o = self.ex.create_order(self.symbol, "market", exit_side, self._amount(qty), None,
                                         {"stopLossPrice": sp, "reduceOnly": True})
            elif self.ex.id == "bybit":
                limit = float(self.ex.price_to_precision(self.symbol, stop * 0.995))
                o = self.ex.create_order(self.symbol, "limit", "sell", self._amount(qty), limit,
                                         {"triggerPrice": sp, "orderFilter": "StopOrder"})
            else:
                limit = float(self.ex.price_to_precision(self.symbol, stop * 0.995))
                o = self.ex.create_order(self.symbol, "STOP_LOSS_LIMIT", "sell", self._amount(qty), limit,
                                         {"stopPrice": sp, "timeInForce": "GTC"})
            self.stop_order_id = o["id"]
        except Exception as e:
            log.warning("could not place exchange-side stop (%s) - bot will manage stop by polling", e)

    def cancel_stop(self) -> None:
        if self.stop_order_id:
            try:
                self.ex.cancel_order(self.stop_order_id, self.symbol)
            except Exception as e:
                log.warning("cancel stop failed: %s", e)
            self.stop_order_id = None
