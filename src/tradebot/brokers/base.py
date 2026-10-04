from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from tradebot.models import Fill


class Broker(ABC):
    """Ejecuta órdenes a mercado sobre un único símbolo spot (sin apalancamiento)."""

    symbol: str
    fee_rate: float

    @property
    @abstractmethod
    def cash(self) -> float:
        """Saldo disponible en moneda de cotización (p. ej. USDT)."""

    @property
    @abstractmethod
    def base_qty(self) -> float:
        """Saldo disponible en moneda base (p. ej. BTC)."""

    @abstractmethod
    def buy(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None: ...

    @abstractmethod
    def sell(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None: ...

    def equity(self, mark_price: float) -> float:
        return self.cash + self.base_qty * mark_price
