"""Bucle en tiempo real para paper trading y modo real, con una o varias monedas.

En cada iteración (cada ``poll_seconds``):
1. Lee el precio actual de cada moneda y comprueba su stop-loss.
2. Para cada moneda cuya vela haya cerrado, la estrategia decide y la orden se ejecuta.
3. Actualiza los cortacircuitos de riesgo (sobre el capital total) y guarda el estado
   en disco con escritura atómica, para sobrevivir a reinicios y cortes.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import ccxt
import pandas as pd

from tradebot.brokers.ccxt_broker import CcxtBroker, OrderUncertainError
from tradebot.brokers.simulated import SimulatedBroker
from tradebot.data import drop_unclosed, ohlcv_to_frame, timeframe_to_timedelta
from tradebot.portfolio import Portfolio

log = logging.getLogger(__name__)


def _utcnow() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


class LiveRunner:
    RETRY_EMPTY = pd.Timedelta(minutes=5)

    def __init__(
        self,
        portfolio: Portfolio,
        data_exchange: ccxt.Exchange,
        timeframe: str,
        state_path: Path,
        poll_seconds: int,
        clock: Callable[[], pd.Timestamp] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.exchange = data_exchange
        self.timeframe = timeframe
        self.tf = timeframe_to_timedelta(timeframe)
        self.state_path = state_path
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.sleep = sleep
        self.notify = notify or (lambda text: None)
        self.lock = threading.Lock()  # step() y los comandos de Telegram no se pisan
        self.last_bar_ts: dict[str, pd.Timestamp] = {}
        # Si una vela "debería" haber cerrado pero no llega (bolsa cerrada, fin de semana,
        # festivo), no se vuelve a preguntar hasta esta hora.
        self.retry_at: dict[str, pd.Timestamp] = {}
        self.history = max(t.strategy.warmup for t in portfolio.traders.values()) + 50
        first = next(iter(portfolio.traders.values()))
        self.mode = "paper" if isinstance(first.broker, SimulatedBroker) else "real/testnet"

    @property
    def symbols(self) -> list[str]:
        return self.portfolio.symbols

    @property
    def last_prices(self) -> dict[str, float]:
        return self.portfolio.prices

    # ----------------------------------------------------------------- state
    def save_state(self) -> None:
        state = {
            "version": 2,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "last_bar_ts": {s: ts.isoformat() for s, ts in self.last_bar_ts.items()},
            "portfolio": self.portfolio.to_dict(),
        }
        first = next(iter(self.portfolio.traders.values()))
        if isinstance(first.broker, SimulatedBroker):
            state["paper_account"] = first.broker.account.to_dict()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.state_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, self.state_path)

    def load_state(self) -> bool:
        if not self.state_path.exists():
            return False
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("version") != 2 or state["timeframe"] != self.timeframe:
            raise RuntimeError(
                f"El estado guardado en {self.state_path} no es compatible "
                f"(versión/timeframe distintos); usa otro state_dir o bórralo conscientemente"
            )
        self.portfolio.load_dict(state["portfolio"])
        first = next(iter(self.portfolio.traders.values()))
        if "paper_account" in state and isinstance(first.broker, SimulatedBroker):
            first.broker.account.load_dict(state["paper_account"])
        self.last_bar_ts = {
            s: pd.Timestamp(ts) for s, ts in state["last_bar_ts"].items() if s in self.symbols
        }
        added = [s for s in self.symbols if s not in state["symbols"]]
        if added:
            log.info("Monedas nuevas desde el último arranque: %s", added)
        log.info(
            "Estado restaurado: posiciones=%s",
            {s: t.position.qty for s, t in self.portfolio.traders.items() if t.position},
        )
        return True

    # ------------------------------------------------------------ market data
    def fetch_closed_bars(self, symbol: str) -> pd.DataFrame:
        rows = self.exchange.fetch_ohlcv(symbol, self.timeframe, limit=self.history)
        return drop_unclosed(ohlcv_to_frame(rows), self.timeframe, self.clock())

    def fetch_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        price = ticker.get("last") or ticker.get("close")
        if not price or price <= 0:
            raise ccxt.ExchangeError(f"Precio inválido en ticker de {symbol}: {ticker}")
        return float(price)

    # ------------------------------------------------------------- lifecycle
    def bootstrap(self) -> None:
        self.load_state()
        self.reconcile()
        for sym in self.symbols:
            if sym not in self.last_bar_ts:
                # No se actúa sobre velas que ya habían cerrado antes de arrancar.
                self.last_bar_ts[sym] = self.fetch_closed_bars(sym).index[-1]
            self.portfolio.mark(sym, self.fetch_price(sym))
        log.info("Listo; última vela analizada por moneda: %s", self.last_bar_ts)
        self.save_state()

    def reconcile(self) -> None:
        """Comprueba que las posiciones coinciden con el saldo real (solo modo real)."""
        for t in self.portfolio.traders.values():
            broker = t.broker
            if not isinstance(broker, CcxtBroker):
                return
            broker.refresh_balance()
            pos = t.position
            if pos and broker.base_qty < pos.qty * 0.98:
                self._halt(
                    f"El exchange tiene {broker.base_qty} {broker.base_ccy} pero el bot esperaba "
                    f"{pos.qty}. ¿Venta manual? Se detiene el bot para revisión."
                )
            elif pos and broker.base_qty > pos.qty * 1.02:
                log.warning("Hay más %s del que gestiona el bot; se ignora", broker.base_ccy)

    def _halt(self, reason: str) -> None:
        state = self.portfolio.risk.state
        state.halted = True
        state.halt_reason = reason
        log.critical("BOT DETENIDO: %s", reason)
        self.notify(f"⛔ BOT DETENIDO\n{reason}")

    def _bar_due(self, symbol: str, now: pd.Timestamp) -> bool:
        """¿Ha cerrado ya la vela siguiente a la última analizada? Evita descargas inútiles."""
        last = self.last_bar_ts.get(symbol)
        if symbol in self.retry_at and now < self.retry_at[symbol]:
            return False
        return last is None or now >= last + 2 * self.tf

    def step(self) -> None:
        with self.lock:
            self._step()

    def _step(self) -> None:
        now = self.clock()
        pf = self.portfolio
        was_halted = pf.risk.state.halted

        for sym, t in pf.traders.items():
            price = self.fetch_price(sym)
            pf.mark(sym, price)
            t.check_stop(price, now)

        evaluated = False
        for sym, t in pf.traders.items():
            if not self._bar_due(sym, now):
                continue
            bars = self.fetch_closed_bars(sym)
            last = self.last_bar_ts.get(sym)
            new_bars = bars[bars.index > last] if last is not None else bars
            if new_bars.empty:
                self.retry_at[sym] = now + self.RETRY_EMPTY
                continue
            self.retry_at.pop(sym, None)
            if len(new_bars) > 1:
                log.warning(
                    "%s: se han perdido %d velas; solo se evalúa la última", sym, len(new_bars) - 1
                )
            prepared = t.strategy.prepare(bars)
            t.on_bar_close(prepared.iloc[-1])
            self.last_bar_ts[sym] = prepared.index[-1]
            evaluated = True

        if evaluated:
            # Las órdenes se ejecutan después de que TODAS las monedas hayan decidido,
            # igual que en el backtest.
            for sym, t in pf.traders.items():
                t.execute_pending(pf.prices[sym], now)
        pf.risk.update(pf.equity(), now)

        if pf.risk.must_flatten:
            pf.flatten_all(now, "cortacircuitos: drawdown máximo")
        if pf.risk.state.halted and not was_halted:
            self.notify(f"⛔ BOT DETENIDO\n{pf.risk.state.halt_reason}")

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
        names = ", ".join(s.split("/")[0] for s in self.symbols)
        log.info("Bot en marcha: %s %s cada %ss", names, self.timeframe, self.poll_seconds)
        first = next(iter(self.portfolio.traders.values()))
        self.notify(
            f"🤖 Bot en marcha ({self.mode})\nVigilando: {names}\n"
            f"Velas de {self.timeframe} · {first.strategy.name} · "
            f"máx. {self.portfolio.max_open_positions} posiciones\n"
            "Escribe /ayuda para ver los comandos."
        )
        steps, wait, errors = 0, self.poll_seconds, 0
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
                except Exception as exc:  # noqa: BLE001 - en un servidor, mejor seguir y avisar
                    errors += 1
                    wait = min(wait * 2, 600)
                    log.exception("Error inesperado")
                    if errors in (1, 10, 100):
                        self.notify(f"⚠️ Error inesperado (#{errors}): {exc}")
                self.sleep(wait)
        except KeyboardInterrupt:
            log.info("Parada manual. Las posiciones siguen abiertas y con su stop guardado.")
        finally:
            self.save_state()

    def _heartbeat(self) -> None:
        pf = self.portfolio
        log.info(
            "Latido: capital=%.2f posiciones=%s operaciones=%d detenido=%s",
            pf.equity(),
            [s for s, t in pf.traders.items() if t.position],
            len(pf.trades()),
            pf.risk.state.halted,
        )
