"""Núcleo de decisión compartido por backtest, paper trading y modo real.

Ciclo por vela (sin mirar al futuro):
1. ``on_bar_close(vela t)``  -> la estrategia decide; la orden queda PENDIENTE.
2. ``execute_pending(precio)`` -> se ejecuta en la apertura de t+1 (o al precio actual en vivo).
3. ``check_stop(precio)``     -> durante la vela, si el precio toca el stop, se vende.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from tradebot.brokers.base import Broker
from tradebot.journal import Journal
from tradebot.models import Action, Fill, Position, Side, Trade
from tradebot.risk import RiskManager
from tradebot.strategies.base import Strategy

if TYPE_CHECKING:
    from tradebot.portfolio import Portfolio

log = logging.getLogger(__name__)


@dataclass
class PendingOrder:
    side: Side
    reason: str
    stop_price: float | None = None

    def to_dict(self) -> dict:
        return {"side": self.side.value, "reason": self.reason, "stop_price": self.stop_price}

    @staticmethod
    def from_dict(d: dict) -> PendingOrder:
        return PendingOrder(Side(d["side"]), d["reason"], d["stop_price"])


class Trader:
    def __init__(
        self,
        symbol: str,
        strategy: Strategy,
        risk: RiskManager,
        broker: Broker,
        journal: Journal | None = None,
    ) -> None:
        self.symbol = symbol
        self.strategy = strategy
        self.risk = risk
        self.broker = broker
        self.journal = journal
        self.position: Position | None = None
        self.pending: PendingOrder | None = None
        self.trades: list[Trade] = []
        self.fills: list[Fill] = []
        # Funciones que reciben un texto en cada compra/venta (p. ej. avisos por Telegram).
        self.listeners: list[Callable[[str], None]] = []
        # Si forma parte de un Portfolio, el riesgo se mide sobre el capital total.
        self.portfolio: Portfolio | None = None

    # ---------------------------------------------------------------- helpers
    def equity(self, mark_price: float) -> float:
        """Capital total (de todo el portfolio si lo hay) con esta moneda a ``mark_price``."""
        if self.portfolio is not None:
            self.portfolio.mark(self.symbol, mark_price)
            return self.portfolio.equity()
        held = self.position.qty if self.position else 0.0
        return self.broker.cash + held * mark_price

    def _can_open(self, equity: float) -> tuple[bool, str]:
        if self.portfolio is not None:
            return self.portfolio.can_open()
        return self.risk.can_open(equity)

    def _emit(self, text: str) -> None:
        for listener in self.listeners:
            try:
                listener(text)
            except Exception:  # noqa: BLE001 - un aviso fallido no puede romper el trading
                log.exception("Error en listener")

    def _record_fill(self, fill: Fill) -> None:
        self.fills.append(fill)
        if self.journal:
            self.journal.log_fill(fill)

    # ------------------------------------------------------------- lifecycle
    def on_bar_close(self, bar: pd.Series) -> None:
        ts: pd.Timestamp = bar.name
        close = float(bar["close"])
        eq = self.equity(close)
        self.risk.update(eq, ts)

        if self.position:
            self.position.bars_held += 1

        if self.risk.must_flatten:
            if self.position:
                self.pending = PendingOrder(Side.SELL, "cortacircuitos: drawdown máximo")
            return

        signal = self.strategy.on_bar(bar, self.position)

        if self.position:
            if signal.action is Action.EXIT:
                self.pending = PendingOrder(Side.SELL, signal.reason)
            elif signal.stop_price is not None and signal.stop_price > self.position.stop_price:
                # El stop solo sube (nunca baja) y nunca queda por encima del precio actual.
                self.position.stop_price = min(signal.stop_price, close)
            return

        if signal.action is Action.ENTER:
            if signal.stop_price is None or signal.stop_price >= close:
                log.warning("Señal de entrada sin stop válido, ignorada: %s", signal)
                return
            allowed, why = self._can_open(eq)
            if not allowed:
                log.info("%s %s: entrada bloqueada (%s)", ts, self.symbol, why)
                return
            self.pending = PendingOrder(Side.BUY, signal.reason, signal.stop_price)

    def execute_pending(self, price: float, ts: pd.Timestamp) -> Fill | None:
        order, self.pending = self.pending, None
        if order is None:
            return None
        if order.side is Side.BUY:
            return self._open(price, ts, order)
        return self._close(price, ts, order.reason)

    def check_stop(
        self, low: float, ts: pd.Timestamp, open_price: float | None = None
    ) -> Fill | None:
        """Si el mínimo toca el stop, vende. Con hueco a la baja se vende a la apertura (peor)."""
        if not self.position or low > self.position.stop_price:
            return None
        exit_price = self.position.stop_price
        if open_price is not None and open_price < exit_price:
            exit_price = open_price
        return self._close(exit_price, ts, "stop-loss")

    def close_position(self, price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        self.pending = None
        return self._close(price, ts, reason)

    # -------------------------------------------------------------- execution
    def _open(self, price: float, ts: pd.Timestamp, order: PendingOrder) -> Fill | None:
        if self.position:
            return None
        assert order.stop_price is not None
        if price <= order.stop_price:
            log.info("%s: el precio (%.2f) ya está bajo el stop; entrada cancelada", ts, price)
            return None
        eq = self.equity(price)
        qty = self.risk.position_size(
            eq, self.broker.cash, price, order.stop_price, self.broker.fee_rate
        )
        if qty <= 0:
            return None
        base_before = self.broker.base_qty
        fill = self.broker.buy(qty, price, ts, order.reason)
        if fill is None:
            return None
        # Cantidad realmente recibida (algunos exchanges cobran la comisión en la moneda base).
        received = self.broker.base_qty - base_before
        held = received if received > 0 else fill.qty
        self.position = Position(
            self.symbol, held, fill.price, order.stop_price, fill.timestamp, entry_fee=fill.fee
        )
        self._record_fill(fill)
        log.info(
            "%s COMPRA %.6f @ %.2f stop=%.2f (%s)",
            ts,
            held,
            fill.price,
            order.stop_price,
            order.reason,
        )
        self._emit(
            f"🟢 COMPRA {self.symbol}\n{held:.6f} @ {fill.price:,.2f}\n"
            f"Stop: {order.stop_price:,.2f}\nMotivo: {order.reason}"
        )
        return fill

    def _close(self, price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        pos = self.position
        if not pos:
            return None
        fill = self.broker.sell(pos.qty, price, ts, reason)
        if fill is None:
            return None
        self._record_fill(fill)
        cost = pos.qty * pos.entry_price
        pnl = fill.qty * fill.price - fill.fee - cost - pos.entry_fee
        trade = Trade(
            self.symbol,
            pos.opened_at,
            fill.timestamp,
            pos.qty,
            pos.entry_price,
            fill.price,
            pos.entry_fee + fill.fee,
            pnl,
            reason,
        )
        self.trades.append(trade)
        if self.journal:
            self.journal.log_trade(trade)
        log.info("%s VENTA %.6f @ %.2f pnl=%.2f (%s)", ts, fill.qty, fill.price, pnl, reason)
        self.position = None
        icon = "✅" if pnl > 0 else "🔴"
        self._emit(
            f"{icon} VENTA {self.symbol}\n{fill.qty:.6f} @ {fill.price:,.2f}\n"
            f"Resultado: {pnl:+,.2f} ({trade.return_pct:+.2%})\nMotivo: {reason}"
        )
        return fill
