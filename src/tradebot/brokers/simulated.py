"""Bróker simulado con comisiones y deslizamiento. Lo usan el backtest y el paper trading.

Con varias monedas, todos los brókers comparten un ``PaperAccount``: el efectivo es único,
igual que en una cuenta real de exchange.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from tradebot.brokers.base import Broker
from tradebot.models import Fill, Side


@dataclass
class PaperAccount:
    cash: float
    holdings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"cash": self.cash, "holdings": dict(self.holdings)}

    def load_dict(self, d: dict) -> None:
        self.cash = float(d["cash"])
        self.holdings = {k: float(v) for k, v in d.get("holdings", {}).items()}


class SimulatedBroker(Broker):
    def __init__(
        self,
        symbol: str,
        initial_cash: float | None,
        fee_rate: float,
        slippage_bps: float,
        qty_step: float = 1e-8,
        account: PaperAccount | None = None,
    ) -> None:
        if account is None and initial_cash is None:
            raise ValueError("Hace falta initial_cash o una cuenta compartida")
        self.symbol = symbol
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10_000
        self.qty_step = qty_step
        self.account = account or PaperAccount(float(initial_cash))

    @property
    def cash(self) -> float:
        return self.account.cash

    @property
    def base_qty(self) -> float:
        return self.account.holdings.get(self.symbol, 0.0)

    def _round_down(self, qty: float) -> float:
        return math.floor(qty / self.qty_step + 1e-9) * self.qty_step

    def buy(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        price = ref_price * (1 + self.slippage)
        affordable = self.account.cash / (price * (1 + self.fee_rate))
        qty = self._round_down(min(qty, affordable))
        if qty <= 0:
            return None
        fee = qty * price * self.fee_rate
        self.account.cash -= qty * price + fee
        self.account.holdings[self.symbol] = self.base_qty + qty
        return Fill(ts, self.symbol, Side.BUY, qty, price, fee, reason)

    def sell(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        qty = self._round_down(min(qty, self.base_qty))
        if qty <= 0:
            return None
        price = ref_price * (1 - self.slippage)
        fee = qty * price * self.fee_rate
        self.account.cash += qty * price - fee
        remaining = self.base_qty - qty
        self.account.holdings[self.symbol] = 0.0 if remaining < self.qty_step else remaining
        return Fill(ts, self.symbol, Side.SELL, qty, price, fee, reason)
