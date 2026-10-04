from __future__ import annotations

import pandas as pd
import pytest
from test_core import ScriptedStrategy

from tradebot.brokers.simulated import SimulatedBroker
from tradebot.config import RiskConfig
from tradebot.models import Action, Signal
from tradebot.risk import RiskManager
from tradebot.runner import LiveRunner
from tradebot.trader import Trader

H4 = 4 * 3600 * 1000


class FakeExchange:
    """Exchange falso: velas de 4h que avanzan con el reloj."""

    def __init__(self, start: pd.Timestamp):
        self.start = start
        self.now = start
        self.price = 100.0

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        n = int((self.now - self.start) / pd.Timedelta(hours=4)) + 1  # incluye la vela abierta
        t0 = int(self.start.value // 1_000_000)
        return [[t0 + i * H4, 100, 101, 99, 100, 1] for i in range(n)][-(limit or n) :]

    def fetch_ticker(self, symbol):
        return {"last": self.price}


def make_runner(tmp_path, script, exchange, state="state.json"):
    broker = SimulatedBroker("BTC/USDT", 10_000, fee_rate=0.001, slippage_bps=0)
    trader = Trader(
        "BTC/USDT",
        ScriptedStrategy(script),
        RiskManager(RiskConfig(max_position_pct=1.0)),
        broker,
    )
    return LiveRunner(
        trader,
        exchange,
        "4h",
        tmp_path / state,
        30,
        clock=lambda: exchange.now,
        sleep=lambda s: None,
    )


def test_runner_acts_only_on_new_closed_bars_and_persists(tmp_path):
    start = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    ex = FakeExchange(start)
    ex.now = start + pd.Timedelta(hours=9)  # velas 00:00 y 04:00 cerradas, 08:00 abierta
    runner = make_runner(tmp_path, {0: Signal(Action.ENTER, 90, "in")}, ex)
    runner.bootstrap()
    assert runner.last_bar_ts == start + pd.Timedelta(hours=4)  # no actúa sobre el pasado

    runner.step()
    assert runner.trader.position is None  # la vela de las 08:00 aún no ha cerrado

    ex.now = start + pd.Timedelta(hours=12, minutes=1)
    runner.step()
    pos = runner.trader.position
    assert pos is not None and pos.stop_price == 90

    # Reinicio: un runner nuevo recupera posición, stop y saldo simulado.
    runner2 = make_runner(tmp_path, {}, ex)
    runner2.bootstrap()
    assert runner2.trader.position.qty == pytest.approx(pos.qty)
    assert runner2.trader.broker.cash == pytest.approx(runner.trader.broker.cash)

    # El stop salta con el precio en vivo, sin esperar al cierre de la vela.
    ex.price = 89.0
    runner2.step()
    assert runner2.trader.position is None
    assert runner2.trader.trades[-1].exit_reason == "stop-loss"


def test_runner_refuses_state_from_other_market(tmp_path):
    ex = FakeExchange(pd.Timestamp("2024-01-01", tz="UTC"))
    ex.now += pd.Timedelta(hours=9)
    make_runner(tmp_path, {}, ex).bootstrap()
    other = make_runner(tmp_path, {}, ex)
    other.symbol = "ETH/USDT"
    with pytest.raises(RuntimeError):
        other.bootstrap()


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
    assert runner.last_bar_ts is not None
