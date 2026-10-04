"""Tipos de datos compartidos por backtest, paper trading y modo real."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

import pandas as pd


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Action(str, Enum):
    ENTER = "enter"
    EXIT = "exit"
    HOLD = "hold"


@dataclass(frozen=True)
class Signal:
    """Decisión de la estrategia al cierre de una vela.

    ``stop_price`` es obligatorio en ENTER (sin stop no hay forma de dimensionar
    el riesgo). En HOLD con posición abierta puede traer un stop nuevo para
    hacerlo "trailing"; el gestor solo lo acepta si sube.
    """

    action: Action
    stop_price: float | None = None
    reason: str = ""

    @staticmethod
    def hold(stop_price: float | None = None) -> Signal:
        return Signal(Action.HOLD, stop_price)


@dataclass
class Fill:
    timestamp: pd.Timestamp
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float  # en moneda de cotización
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    stop_price: float
    opened_at: pd.Timestamp
    entry_fee: float = 0.0
    bars_held: int = 0  # velas cerradas desde la entrada

    def to_dict(self) -> dict:
        d = asdict(self)
        d["opened_at"] = self.opened_at.isoformat()
        return d

    @staticmethod
    def from_dict(d: dict) -> Position:
        return Position(**{**d, "opened_at": pd.Timestamp(d["opened_at"])})


@dataclass
class Trade:
    """Operación completa (entrada + salida) para el diario y las métricas."""

    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    qty: float
    entry_price: float
    exit_price: float
    fees: float
    pnl: float  # neto de comisiones
    exit_reason: str

    @property
    def return_pct(self) -> float:
        cost = self.qty * self.entry_price
        return self.pnl / cost if cost else 0.0
