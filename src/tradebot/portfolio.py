"""Varias monedas con un único capital y un único control de riesgo.

Cada moneda tiene su ``Trader`` (estrategia, posición, stop), pero:
- el efectivo es compartido (una sola cuenta),
- los límites de pérdida diaria y drawdown se miden sobre el capital TOTAL,
- el tamaño de cada operación se calcula sobre el capital total,
- nunca hay más de ``max_open_positions`` posiciones abiertas a la vez.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from tradebot.models import Side
from tradebot.risk import RiskManager, RiskState
from tradebot.trader import PendingOrder, Trader


class Portfolio:
    def __init__(self, traders: Iterable[Trader], risk: RiskManager, max_open_positions: int):
        self.traders: dict[str, Trader] = {}
        self.risk = risk
        self.max_open_positions = max_open_positions
        self.prices: dict[str, float] = {}
        for t in traders:
            if t.symbol in self.traders:
                raise ValueError(f"Símbolo repetido: {t.symbol}")
            t.risk = risk
            t.portfolio = self
            self.traders[t.symbol] = t

    # -------------------------------------------------------------- valuation
    @property
    def symbols(self) -> list[str]:
        return list(self.traders)

    def mark(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price

    @property
    def cash(self) -> float:
        # Todos los brókers comparten cuenta, así que cualquiera da el mismo efectivo.
        return next(iter(self.traders.values())).broker.cash

    def positions_value(self) -> float:
        total = 0.0
        for sym, t in self.traders.items():
            if t.position:
                total += t.position.qty * self.prices.get(sym, t.position.entry_price)
        return total

    def equity(self) -> float:
        return self.cash + self.positions_value()

    # ------------------------------------------------------------------- risk
    def open_count(self) -> int:
        """Posiciones abiertas más compras ya decididas pendientes de ejecutar."""
        return sum(
            1
            for t in self.traders.values()
            if t.position or (t.pending is not None and t.pending.side is Side.BUY)
        )

    def can_open(self) -> tuple[bool, str]:
        allowed, why = self.risk.can_open(self.equity())
        if not allowed:
            return allowed, why
        if self.open_count() >= self.max_open_positions:
            return False, f"ya hay {self.max_open_positions} posiciones abiertas (máximo)"
        return True, ""

    # ---------------------------------------------------------------- helpers
    def add_listener(self, fn: Callable[[str], None]) -> None:
        for t in self.traders.values():
            t.listeners.append(fn)

    def flatten_all(self, ts, reason: str) -> None:
        for sym, t in self.traders.items():
            if t.position and sym in self.prices:
                t.close_position(self.prices[sym], ts, reason)

    def trades(self) -> list:
        out = [tr for t in self.traders.values() for tr in t.trades]
        return sorted(out, key=lambda tr: tr.exit_time)

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {
            "risk": self.risk.state.to_dict(),
            "traders": {
                sym: {
                    "position": t.position.to_dict() if t.position else None,
                    "pending": t.pending.to_dict() if t.pending else None,
                }
                for sym, t in self.traders.items()
            },
        }

    def load_dict(self, d: dict) -> None:
        from tradebot.models import Position

        self.risk.state = RiskState.from_dict(d["risk"])
        saved = d.get("traders", {})
        orphans = [s for s, v in saved.items() if v.get("position") and s not in self.traders]
        if orphans:
            raise RuntimeError(
                f"Hay posiciones abiertas en {orphans} pero ya no están en la configuración. "
                "Vuelve a añadirlas hasta que se cierren."
            )
        for sym, t in self.traders.items():
            v = saved.get(sym, {})
            t.position = Position.from_dict(v["position"]) if v.get("position") else None
            t.pending = PendingOrder.from_dict(v["pending"]) if v.get("pending") else None
