"""Bróker real (o testnet) a través de ccxt. Solo órdenes a mercado spot.

Decisiones de seguridad:
- Las órdenes NO se reintentan automáticamente: si hay un error de red al enviarla
  no sabemos si llegó, y reintentar podría duplicarla. En ese caso se lanza
  ``OrderUncertainError`` y el motor reconcilia con el saldo real del exchange.
- Las cantidades se redondean a la precisión del mercado y se validan los mínimos.
"""

from __future__ import annotations

import logging
import time
import uuid

import ccxt
import pandas as pd

from tradebot.brokers.base import Broker
from tradebot.models import Fill, Side

log = logging.getLogger(__name__)


class OrderUncertainError(RuntimeError):
    """No se sabe si la orden se ejecutó. Hay que reconciliar con el exchange."""


class CcxtBroker(Broker):
    def __init__(self, exchange: ccxt.Exchange, symbol: str, fee_rate: float) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.fee_rate = fee_rate
        exchange.load_markets()
        self.market = exchange.market(symbol)
        self.base_ccy = self.market["base"]
        self.quote_ccy = self.market["quote"]
        self._balance: dict = {}
        self.refresh_balance()

    # -------------------------------------------------------------- balances
    def refresh_balance(self) -> None:
        self._balance = self.exchange.fetch_balance()

    @property
    def cash(self) -> float:
        return float(self._balance.get("free", {}).get(self.quote_ccy) or 0.0)

    @property
    def base_qty(self) -> float:
        return float(self._balance.get("free", {}).get(self.base_ccy) or 0.0)

    # ---------------------------------------------------------------- orders
    def _prepare_qty(self, qty: float, price: float) -> float:
        try:
            qty = float(self.exchange.amount_to_precision(self.symbol, qty))
        except ccxt.InvalidOrder:  # por debajo de la precisión mínima
            return 0.0
        limits = self.market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min") or 0.0
        min_cost = (limits.get("cost") or {}).get("min") or 0.0
        if qty < min_amount or qty * price < min_cost:
            log.warning("Orden por debajo del mínimo del exchange (qty=%s)", qty)
            return 0.0
        return qty

    def _fee_in_quote(self, order: dict, price: float) -> float:
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        total = 0.0
        for fee in fees:
            cost = float(fee.get("cost") or 0.0)
            ccy = fee.get("currency")
            if ccy == self.quote_ccy:
                total += cost
            elif ccy == self.base_ccy:
                total += cost * price
            else:  # p. ej. comisiones pagadas en BNB: estimamos con la tasa configurada
                total += float(order.get("cost") or 0.0) * self.fee_rate
        return total

    def _wait_filled(self, order: dict, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while order.get("status") not in ("closed", "canceled", "expired", "rejected"):
            if time.monotonic() > deadline:
                raise OrderUncertainError(f"Orden {order.get('id')} sin confirmar tras {timeout}s")
            time.sleep(1.0)
            order = self.exchange.fetch_order(order["id"], self.symbol)
        return order

    def _market(self, side: Side, qty: float, ref_price: float, ts, reason: str) -> Fill | None:
        qty = self._prepare_qty(qty, ref_price)
        if qty <= 0:
            return None
        client_id = f"tb{uuid.uuid4().hex[:20]}"
        log.info(
            "ENVIANDO %s %s %s (~%.2f) motivo=%s", side.value, qty, self.symbol, ref_price, reason
        )
        try:
            order = self.exchange.create_order(
                self.symbol, "market", side.value, qty, None, {"clientOrderId": client_id}
            )
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            raise OrderUncertainError(f"Error de red enviando orden {client_id}: {exc}") from exc

        order = self._wait_filled(order)
        filled = float(order.get("filled") or 0.0)
        self.refresh_balance()
        if filled <= 0:
            log.error("Orden %s no ejecutada: estado=%s", order.get("id"), order.get("status"))
            return None
        price = float(order.get("average") or order.get("price") or ref_price)
        fee = self._fee_in_quote(order, price)
        fill_ts = (
            pd.Timestamp(order["timestamp"], unit="ms", tz="UTC") if order.get("timestamp") else ts
        )
        return Fill(fill_ts, self.symbol, side, filled, price, fee, reason)

    def buy(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        return self._market(Side.BUY, qty, ref_price, ts, reason)

    def sell(self, qty: float, ref_price: float, ts: pd.Timestamp, reason: str) -> Fill | None:
        return self._market(Side.SELL, min(qty, self.base_qty), ref_price, ts, reason)
