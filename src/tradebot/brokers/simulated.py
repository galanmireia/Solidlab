"""Bróker simulado con comisiones y deslizamiento. Lo usan el backtest y el paper trading."""

from __future__ import annotations

import math

import pandas as pd

from tradebot.brokers.base import Broker
from tradebot.models import Fill, Side


class SimulatedBroker(Broker):
    def __init__(
        self,
        symbol: str,
        initial_cash: float,
        fee_rate: float,
        slippage_bps: float,
        qty_step: float = 1e-8,
    ) -> None:
        self.symbol = symbol
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10_000
        self.qty_step = qty_step
        self._cash = float(initial_cash)
        self._base = 0.0

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def base_qty(self) -> float:
        return self._base

    def _round_down(self, qty: float) -> float:
        return math.floor(qty / self.qty_step + 1e-9) * self.qty_step

    def buy(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        price = ref_price * (1 + self.slippage)
        affordable = self._cash / (price * (1 + self.fee_rate))
        qty = self._round_down(min(qty, affordable))
        if qty <= 0:
            return None
        fee = qty * price * self.fee_rate
        self._cash -= qty * price + fee
        self._base += qty
        return Fill(ts, self.symbol, Side.BUY, qty, price, fee, reason)

    def sell(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        qty = self._round_down(min(qty, self._base))
        if qty <= 0:
            return None
        price = ref_price * (1 - self.slippage)
        fee = qty * price * self.fee_rate
        self._cash += qty * price - fee
        self._base -= qty
        if self._base < self.qty_step:  # restos por redondeo
            self._base = 0.0
        return Fill(ts, self.symbol, Side.SELL, qty, price, fee, reason)

    # Persistencia para paper trading
    def to_dict(self) -> dict:
        return {"cash": self._cash, "base": self._base}

    def load_dict(self, d: dict) -> None:
        self._cash = float(d["cash"])
        self._base = float(d["base"])
