from __future__ import annotations

import pandas as pd
import pytest
from test_core import ScriptedStrategy

from tradebot.brokers.simulated import PaperAccount, SimulatedBroker
from tradebot.config import RiskConfig
from tradebot.models import Action, Signal
from tradebot.portfolio import Portfolio
from tradebot.risk import RiskManager
from tradebot.runner import LiveRunner
from tradebot.trader import Trader

H4 = 4 * 3600 * 1000
BTC, ETH = "BTC/USDT", "ETH/USDT"


class FakeExchange:
    """Exchange falso: velas de 4h que avanzan con el reloj; un precio por moneda."""

    def __init__(self, start: pd.Timestamp):
        self.start = start
        self.now = start
        self.prices: dict[str, float] = {}
        self.ohlcv_calls = 0

    @property
    def price(self) -> float:  # atajo para tests de una sola moneda
        return self.prices.get(BTC, 100.0)

    @price.setter
    def price(self, value: float) -> None:
        self.prices[BTC] = value

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        self.ohlcv_calls += 1
        n = int((self.now - self.start) / pd.Timedelta(hours=4)) + 1  # incluye la vela abierta
        t0 = int(self.start.value // 1_000_000)
        return [[t0 + i * H4, 100, 101, 99, 100, 1] for i in range(n)][-(limit or n) :]

    def fetch_ticker(self, symbol):
        return {"last": self.prices.get(symbol, 100.0)}


def make_runner(tmp_path, scripts, exchange, state="state.json", max_open=3, timeframe="4h"):
    """``scripts``: guion de BTC, o dict {símbolo: guion} para varias monedas."""
    if not scripts or not isinstance(next(iter(scripts)), str):
        scripts = {BTC: scripts}
    account = PaperAccount(10_000)
    traders = [
        Trader(
            sym,
            ScriptedStrategy(script),
            RiskManager(RiskConfig()),
            SimulatedBroker(sym, None, fee_rate=0.001, slippage_bps=0, account=account),
        )
        for sym, script in scripts.items()
    ]
    portfolio = Portfolio(traders, RiskManager(RiskConfig(max_position_pct=1.0)), max_open)
    return LiveRunner(
        portfolio,
        exchange,
        timeframe,
        tmp_path / state,
        30,
        clock=lambda: exchange.now,
        sleep=lambda s: None,
    )


def btc(runner) -> Trader:
    return runner.portfolio.traders[BTC]


def test_runner_acts_only_on_new_closed_bars_and_persists(tmp_path):
    start = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    ex = FakeExchange(start)
    ex.now = start + pd.Timedelta(hours=9)  # velas 00:00 y 04:00 cerradas, 08:00 abierta
    runner = make_runner(tmp_path, {0: Signal(Action.ENTER, 90, "in")}, ex)
    runner.bootstrap()
    assert runner.last_bar_ts[BTC] == start + pd.Timedelta(hours=4)  # no actúa sobre el pasado

    calls = ex.ohlcv_calls
    runner.step()
    assert btc(runner).position is None  # la vela de las 08:00 aún no ha cerrado
    assert ex.ohlcv_calls == calls  # ni siquiera descarga velas si no toca

    ex.now = start + pd.Timedelta(hours=12, minutes=1)
    runner.step()
    pos = btc(runner).position
    assert pos is not None and pos.stop_price == 90

    # Reinicio: un runner nuevo recupera posición, stop y saldo simulado.
    runner2 = make_runner(tmp_path, {}, ex)
    runner2.bootstrap()
    assert btc(runner2).position.qty == pytest.approx(pos.qty)
    assert runner2.portfolio.cash == pytest.approx(runner.portfolio.cash)

    # El stop salta con el precio en vivo, sin esperar al cierre de la vela.
    ex.price = 89.0
    runner2.step()
    assert btc(runner2).position is None
    assert btc(runner2).trades[-1].exit_reason == "stop-loss"


def test_multiple_coins_share_cash_and_respect_max_positions(tmp_path):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    ex = FakeExchange(start)
    ex.now = start + pd.Timedelta(hours=9)
    enter = {0: Signal(Action.ENTER, 90, "in")}
    runner = make_runner(tmp_path, {BTC: enter, ETH: enter, "SOL/USDT": enter}, ex, max_open=2)
    runner.bootstrap()
    ex.now = start + pd.Timedelta(hours=12, minutes=1)
    runner.step()

    pf = runner.portfolio
    opened = [s for s, t in pf.traders.items() if t.position]
    assert len(opened) == 2  # la tercera señal se bloquea por el máximo de posiciones
    assert pf.cash < 10_000  # efectivo único, compartido
    spent = sum(t.position.qty * t.position.entry_price for t in pf.traders.values() if t.position)
    assert pf.cash + spent == pytest.approx(10_000, rel=0.01)

    # Stop en una sola moneda: la otra sigue abierta.
    ex.prices[opened[0]] = 80.0
    runner.step()
    assert pf.traders[opened[0]].position is None
    assert pf.traders[opened[1]].position is not None


def test_state_with_open_position_in_removed_coin_is_refused(tmp_path):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    ex = FakeExchange(start)
    ex.now = start + pd.Timedelta(hours=9)
    runner = make_runner(tmp_path, {ETH: {0: Signal(Action.ENTER, 90, "in")}}, ex)
    runner.bootstrap()
    ex.now += pd.Timedelta(hours=3, minutes=1)
    runner.step()
    assert runner.portfolio.traders[ETH].position is not None

    with pytest.raises(RuntimeError, match="ETH"):
        make_runner(tmp_path, {BTC: {}}, ex).bootstrap()
    # Añadir monedas nuevas sí está permitido.
    grown = make_runner(tmp_path, {ETH: {}, BTC: {}}, ex)
    grown.bootstrap()
    assert grown.portfolio.traders[ETH].position is not None
    assert BTC in grown.last_bar_ts


def test_runner_refuses_state_with_other_timeframe(tmp_path):
    ex = FakeExchange(pd.Timestamp("2024-01-01", tz="UTC"))
    ex.now += pd.Timedelta(hours=9)
    make_runner(tmp_path, {}, ex).bootstrap()
    with pytest.raises(RuntimeError):
        make_runner(tmp_path, {}, ex, timeframe="1h").bootstrap()


def test_bootstrap_retries_on_network_errors(tmp_path):
    import ccxt

    ex = FakeExchange(pd.Timestamp("2024-01-01", tz="UTC"))
    ex.now += pd.Timedelta(hours=9)
    real_fetch, calls = ex.fetch_ohlcv, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ccxt.NetworkError("caído")
        return real_fetch(*a, **k)

    ex.fetch_ohlcv = flaky
    runner = make_runner(tmp_path, {}, ex)
    runner.run(max_steps=1)
    assert BTC in runner.last_bar_ts
