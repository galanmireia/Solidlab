"""Bucle en tiempo real para paper trading y modo real.

En cada iteración (cada ``poll_seconds``):
1. Lee el precio actual y comprueba el stop-loss.
2. Si ha cerrado una vela nueva, la estrategia decide y la orden se ejecuta al momento.
3. Actualiza los cortacircuitos de riesgo y guarda el estado en disco (escritura atómica),
   para que un reinicio o un corte de luz no haga perder la posición ni el stop.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import ccxt
import pandas as pd

from tradebot.brokers.ccxt_broker import CcxtBroker, OrderUncertainError
from tradebot.brokers.simulated import SimulatedBroker
from tradebot.data import drop_unclosed, ohlcv_to_frame
from tradebot.trader import Trader

log = logging.getLogger(__name__)


def _utcnow() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


class LiveRunner:
    def __init__(
        self,
        trader: Trader,
        data_exchange: ccxt.Exchange,
        timeframe: str,
        state_path: Path,
        poll_seconds: int,
        clock: Callable[[], pd.Timestamp] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.trader = trader
        self.exchange = data_exchange
        self.symbol = trader.symbol
        self.timeframe = timeframe
        self.state_path = state_path
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.sleep = sleep
        self.last_bar_ts: pd.Timestamp | None = None
        self.history = trader.strategy.warmup + 50

    # ----------------------------------------------------------------- state
    def save_state(self) -> None:
        state = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "last_bar_ts": self.last_bar_ts.isoformat() if self.last_bar_ts is not None else None,
            "trader": self.trader.to_dict(),
        }
        if isinstance(self.trader.broker, SimulatedBroker):
            state["paper_broker"] = self.trader.broker.to_dict()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.state_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, self.state_path)

    def load_state(self) -> bool:
        if not self.state_path.exists():
            return False
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state["symbol"] != self.symbol or state["timeframe"] != self.timeframe:
            raise RuntimeError(
                f"El estado guardado es de {state['symbol']} {state['timeframe']}; "
                f"usa otro state_dir o bórralo conscientemente"
            )
        self.trader.load_dict(state["trader"])
        if "paper_broker" in state and isinstance(self.trader.broker, SimulatedBroker):
            self.trader.broker.load_dict(state["paper_broker"])
        if state["last_bar_ts"]:
            self.last_bar_ts = pd.Timestamp(state["last_bar_ts"])
        log.info("Estado restaurado: posición=%s", self.trader.position)
        return True

    # ------------------------------------------------------------ market data
    def fetch_closed_bars(self) -> pd.DataFrame:
        rows = self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=self.history)
        return drop_unclosed(ohlcv_to_frame(rows), self.timeframe, self.clock())

    def fetch_price(self) -> float:
        ticker = self.exchange.fetch_ticker(self.symbol)
        price = ticker.get("last") or ticker.get("close")
        if not price or price <= 0:
            raise ccxt.ExchangeError(f"Precio inválido en ticker: {ticker}")
        return float(price)

    # ------------------------------------------------------------- lifecycle
    def bootstrap(self) -> None:
        restored = self.load_state()
        self.reconcile()
        if not restored:
            bars = self.fetch_closed_bars()
            # No se actúa sobre velas que ya habían cerrado antes de arrancar.
            self.last_bar_ts = bars.index[-1]
            log.info(
                "Arranque limpio; esperando el cierre de la vela posterior a %s", self.last_bar_ts
            )
        self.save_state()

    def reconcile(self) -> None:
        """Comprueba que la posición que cree el bot coincide con el saldo real (solo modo real)."""
        broker = self.trader.broker
        if not isinstance(broker, CcxtBroker):
            return
        broker.refresh_balance()
        pos = self.trader.position
        if pos and broker.base_qty < pos.qty * 0.98:
            msg = (
                f"El exchange tiene {broker.base_qty} {broker.base_ccy} pero el bot esperaba "
                f"{pos.qty}. ¿Venta manual? Se detiene el bot para revisión."
            )
            self._halt(msg)
        elif pos and broker.base_qty > pos.qty * 1.02:
            log.warning(
                "Hay más %s en la cuenta del que gestiona el bot; se ignora el exceso",
                broker.base_ccy,
            )

    def _halt(self, reason: str) -> None:
        state = self.trader.risk.state
        state.halted = True
        state.halt_reason = reason
        log.critical("BOT DETENIDO: %s", reason)

    def step(self) -> None:
        now = self.clock()
        price = self.fetch_price()
        trader = self.trader

        trader.check_stop(price, now)

        bars = self.fetch_closed_bars()
        new_bars = bars[bars.index > self.last_bar_ts] if self.last_bar_ts is not None else bars
        if not new_bars.empty:
            if len(new_bars) > 1:
                log.warning(
                    "Se han perdido %d velas (¿bot parado?); solo se evalúa la última",
                    len(new_bars) - 1,
                )
            prepared = trader.strategy.prepare(bars)
            trader.on_bar_close(prepared.iloc[-1])
            self.last_bar_ts = prepared.index[-1]
            trader.execute_pending(price, now)
        else:
            trader.risk.update(trader.equity(price), now)

        if trader.risk.must_flatten and trader.position:
            trader.close_position(price, now, "cortacircuitos: drawdown máximo")

        self.save_state()

    def _bootstrap_with_retries(self, attempts: int = 6) -> None:
        for i in range(attempts):
            try:
                self.bootstrap()
                return
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
                if i == attempts - 1:
                    raise
                wait = min(2 ** (i + 2), 300)
                log.warning(
                    "No se puede conectar con el exchange (%s). Reintento en %ss", exc, wait
                )
                self.sleep(wait)

    def run(self, max_steps: int | None = None) -> None:
        self._bootstrap_with_retries()
        log.info("Bot en marcha: %s %s cada %ss", self.symbol, self.timeframe, self.poll_seconds)
        steps, wait = 0, self.poll_seconds
        try:
            while max_steps is None or steps < max_steps:
                steps += 1
                try:
                    self.step()
                    wait = self.poll_seconds
                    if steps % max(1, 3600 // self.poll_seconds) == 0:
                        self._heartbeat()
                except OrderUncertainError as exc:
                    self._halt(f"orden en estado desconocido, revisa el exchange: {exc}")
                    self.save_state()
                except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
                    wait = min(wait * 2, 600)
                    log.warning("Error de red: %s. Reintento en %ss", exc, wait)
                self.sleep(wait)
        except KeyboardInterrupt:
            log.info("Parada manual. La posición (si hay) sigue abierta y con su stop guardado.")
        finally:
            self.save_state()

    def _heartbeat(self) -> None:
        t = self.trader
        price = self.fetch_price()
        log.info(
            "Latido: precio=%.2f capital=%.2f posición=%s operaciones=%d detenido=%s",
            price,
            t.equity(price),
            t.position,
            len(t.trades),
            t.risk.state.halted,
        )
