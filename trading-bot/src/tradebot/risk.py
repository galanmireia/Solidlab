"""Gestión de riesgo: tamaño de posición y "cortacircuitos".

Reglas:
1. Cada operación arriesga como máximo ``risk_per_trade`` del capital si salta el stop.
2. Ninguna posición supera ``max_position_pct`` del capital (sin apalancamiento).
3. Pérdida diaria >= ``max_daily_loss_pct`` -> no se abren operaciones hasta el día siguiente (UTC).
4. Caída desde el máximo >= ``max_drawdown_pct`` -> el bot se DETIENE y cierra posiciones.
   Solo se reanuda borrando el estado a mano, tras revisar qué ha pasado.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

from tradebot.config import RiskConfig

log = logging.getLogger(__name__)


@dataclass
class RiskState:
    peak_equity: float | None = None
    day: str | None = None
    day_start_equity: float | None = None
    halted: bool = False
    halt_reason: str = ""
    flatten: bool = False  # además de detenerse, cerrar posiciones (solo por drawdown)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(d: dict) -> RiskState:
        return RiskState(**d)


class RiskManager:
    def __init__(self, cfg: RiskConfig, state: RiskState | None = None) -> None:
        self.cfg = cfg
        self.state = state or RiskState()

    # ---------------------------------------------------------------- sizing
    def position_size(
        self,
        equity: float,
        cash: float,
        entry_price: float,
        stop_price: float,
        fee_rate: float,
    ) -> float:
        """Cantidad a comprar (en moneda base). Devuelve 0 si la operación no es válida."""
        if not (math.isfinite(entry_price) and math.isfinite(stop_price)):
            return 0.0
        if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price or equity <= 0:
            return 0.0

        # Pérdida por unidad si salta el stop, incluyendo comisiones de entrada y salida.
        loss_per_unit = (entry_price - stop_price) + fee_rate * (entry_price + stop_price)
        qty_by_risk = equity * self.cfg.risk_per_trade / loss_per_unit
        qty_by_cap = equity * self.cfg.max_position_pct / entry_price
        qty_by_cash = cash / (entry_price * (1 + fee_rate))
        qty = max(0.0, min(qty_by_risk, qty_by_cap, qty_by_cash))

        if qty * entry_price < self.cfg.min_order_notional:
            return 0.0
        return qty

    # ------------------------------------------------------------- breakers
    def update(self, equity: float, now: pd.Timestamp) -> None:
        """Llamar en cada cierre de vela (y en cada comprobación en vivo) con el capital actual."""
        s = self.state
        day = now.tz_convert("UTC").strftime("%Y-%m-%d") if now.tzinfo else now.strftime("%Y-%m-%d")
        if s.day != day:
            s.day = day
            s.day_start_equity = equity
        s.peak_equity = equity if s.peak_equity is None else max(s.peak_equity, equity)

        if not s.halted and s.peak_equity > 0:
            drawdown = 1 - equity / s.peak_equity
            if drawdown >= self.cfg.max_drawdown_pct:
                s.halted = True
                s.flatten = True
                s.halt_reason = (
                    f"drawdown {drawdown:.1%} >= límite {self.cfg.max_drawdown_pct:.0%} ({now})"
                )
                log.critical("BOT DETENIDO: %s", s.halt_reason)

    def daily_loss_hit(self, equity: float) -> bool:
        s = self.state
        if not s.day_start_equity:
            return False
        return 1 - equity / s.day_start_equity >= self.cfg.max_daily_loss_pct

    def can_open(self, equity: float) -> tuple[bool, str]:
        if self.state.halted:
            return False, f"detenido: {self.state.halt_reason}"
        if self.daily_loss_hit(equity):
            return False, "límite de pérdida diaria alcanzado"
        return True, ""

    @property
    def must_flatten(self) -> bool:
        return self.state.flatten
