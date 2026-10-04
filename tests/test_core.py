from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
from conftest import make_bars
from pydantic import ValidationError

from tradebot.backtest import run_backtest
from tradebot.brokers.simulated import SimulatedBroker
from tradebot.config import AppConfig, RiskConfig
from tradebot.data import drop_unclosed, synthetic_ohlcv, validate
from tradebot.indicators import atr, ema, rsi
from tradebot.metrics import max_drawdown
from tradebot.models import Action, Position, Signal
from tradebot.risk import RiskManager
from tradebot.strategies import build_strategy
from tradebot.strategies.base import Strategy


class ScriptedStrategy(Strategy):
    """Estrategia de prueba: acciones fijadas por número de vela."""

    name = "scripted"

    def __init__(self, script: dict[int, Signal]) -> None:
        super().__init__()
        self.script = script
        self.i = -1

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {}

    @property
    def warmup(self) -> int:
        return 0

    def prepare(self, df):
        return df

    def on_bar(self, bar, position: Position | None) -> Signal:
        self.i += 1
        return self.script.get(self.i, Signal.hold())


# ----------------------------------------------------------------- config
def test_config_rejects_dangerous_risk():
    with pytest.raises(ValidationError):
        RiskConfig(risk_per_trade=1.0)  # 100 % por operación: error de tipeo típico
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"risk": {"risk_per_trade": 0.02, "max_daily_loss_pct": 0.01}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"riks": {}})  # claves desconocidas no se ignoran


def test_live_disabled_and_testnet_by_default():
    cfg = AppConfig()
    assert cfg.live.enabled is False
    assert cfg.exchange.testnet is True


# -------------------------------------------------------------- indicators
def test_indicators_are_causal_and_bounded():
    df = synthetic_ohlcv(600)
    r = rsi(df["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    assert (atr(df).dropna() > 0).all()
    # Cambiar el futuro no puede cambiar el pasado.
    altered = df.copy()
    altered.iloc[400:, :4] *= 2
    for fn in (lambda d: ema(d["close"], 20), lambda d: rsi(d["close"]), atr):
        pd.testing.assert_series_equal(fn(df).iloc[:400], fn(altered).iloc[:400])


# --------------------------------------------------------------------- risk
def test_position_size_risks_exactly_one_percent():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_position_pct=1.0, min_order_notional=0))
    qty = rm.position_size(equity=10_000, cash=10_000, entry_price=100, stop_price=95, fee_rate=0.0)
    assert qty == pytest.approx(20)  # 20 * (100 - 95) = 100 = 1 % de 10 000


def test_position_size_caps_and_invalid_stops():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_position_pct=0.5, min_order_notional=10))
    # Stop muy cercano -> el límite de tamaño manda.
    qty = rm.position_size(10_000, 10_000, entry_price=100, stop_price=99.9, fee_rate=0.001)
    assert qty * 100 == pytest.approx(5_000)
    # Sin efectivo suficiente.
    assert rm.position_size(10_000, 1_000, 100, 99.9, 0.001) * 100 <= 1_000
    # Stops inválidos o por encima del precio.
    assert rm.position_size(10_000, 10_000, 100, 100, 0.0) == 0
    assert rm.position_size(10_000, 10_000, 100, 101, 0.0) == 0
    assert rm.position_size(10_000, 10_000, 100, float("nan"), 0.0) == 0
    # Orden demasiado pequeña.
    assert rm.position_size(100, 100, 100, 1, 0.0) == 0


def test_daily_loss_resets_next_day_and_drawdown_halts():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03, max_drawdown_pct=0.15))
    t = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    rm.update(10_000, t)
    rm.update(9_650, t + pd.Timedelta(hours=5))
    assert rm.can_open(9_650)[0] is False
    rm.update(9_650, t + pd.Timedelta(days=1))
    assert rm.can_open(9_650)[0] is True
    rm.update(8_400, t + pd.Timedelta(days=2))  # -16 % desde el máximo
    assert rm.state.halted and rm.must_flatten
    rm.update(20_000, t + pd.Timedelta(days=3))
    assert rm.can_open(20_000)[0] is False  # no se reactiva solo


# ------------------------------------------------------------------- broker
def test_simulated_broker_costs_and_limits():
    b = SimulatedBroker("BTC/USDT", 1_000, fee_rate=0.001, slippage_bps=10)
    fill = b.buy(100, ref_price=100, ts=pd.Timestamp.now(tz="UTC"), reason="t")
    assert fill.price == pytest.approx(100.1)
    assert b.cash >= 0  # nunca gasta más de lo que tiene
    assert fill.qty < 10
    sell = b.sell(999, ref_price=100, ts=pd.Timestamp.now(tz="UTC"), reason="t")
    assert sell.qty == pytest.approx(fill.qty)  # no vende más de lo que tiene
    assert b.base_qty == 0
    assert b.cash < 1_000  # ida y vuelta al mismo precio pierde comisiones y deslizamiento


# ----------------------------------------------------------------- backtest
def test_orders_execute_at_next_open_not_same_close(cfg: AppConfig):
    bars = make_bars(
        [
            (100, 101, 99, 100),
            (110, 112, 109, 111),  # la entrada decidida en la vela 0 se ejecuta aquí, a 110
            (111, 113, 110, 112),
            (120, 121, 119, 120),  # la salida decidida en la vela 2 se ejecuta aquí, a 120
        ]
    )
    strat = ScriptedStrategy(
        {0: Signal(Action.ENTER, 90, "in"), 2: Signal(Action.EXIT, reason="out")}
    )
    res = run_backtest(bars, cfg, strategy=strat)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.entry_price == pytest.approx(110)
    assert t.exit_price == pytest.approx(120)
    assert t.entry_time == bars.index[1]


def test_stop_gap_fills_at_open(cfg: AppConfig):
    bars = make_bars(
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),  # entrada a 100, stop 95
            (90, 92, 85, 91),  # abre por debajo del stop: se vende a 90, no a 95
        ]
    )
    res = run_backtest(bars, cfg, strategy=ScriptedStrategy({0: Signal(Action.ENTER, 95, "in")}))
    t = res.trades[0]
    assert t.exit_reason == "stop-loss"
    assert t.exit_price == pytest.approx(90)


def test_stop_intrabar_and_trailing_only_moves_up(cfg: AppConfig):
    bars = make_bars(
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),  # entrada, stop 95
            (100, 106, 99, 105),  # al cierre sube el stop a 98
            (105, 106, 103, 104),  # intenta bajarlo a 90: se ignora
            (104, 104, 97, 99),  # toca 98 -> sale a 98
        ]
    )
    strat = ScriptedStrategy(
        {
            0: Signal(Action.ENTER, 95, "in"),
            2: Signal.hold(98),
            3: Signal.hold(90),
        }
    )
    res = run_backtest(bars, cfg, strategy=strat)
    assert res.trades[0].exit_price == pytest.approx(98)


def test_entry_cancelled_when_price_opens_below_stop(cfg: AppConfig):
    bars = make_bars([(100, 101, 99, 100), (94, 95, 90, 92), (92, 93, 91, 92)])
    res = run_backtest(bars, cfg, strategy=ScriptedStrategy({0: Signal(Action.ENTER, 95, "in")}))
    assert res.trades == []


@pytest.mark.parametrize("name", ["ema_trend", "rsi_reversion"])
def test_full_backtest_accounting_is_consistent(name: str):
    cfg = AppConfig.model_validate({"strategy": {"name": name}})
    df = synthetic_ohlcv(3000, seed=7)
    res = run_backtest(df, cfg)
    assert len(res.trades) > 0
    pnl = sum(t.pnl for t in res.trades)
    assert res.equity.iloc[-1] == pytest.approx(cfg.backtest.initial_cash + pnl, rel=1e-9)
    # Ninguna operación pierde mucho más que el riesgo fijado (margen por huecos de precio).
    worst = min(t.pnl for t in res.trades)
    assert worst > -cfg.backtest.initial_cash * cfg.risk.risk_per_trade * 3
    assert 0 <= res.metrics["max_drawdown"] < 1


def test_strategy_rejects_unknown_params():
    with pytest.raises(ValueError):
        build_strategy("ema_trend", {"fsat": 10})
    with pytest.raises(ValueError):
        build_strategy("no_existe")


# --------------------------------------------------------------------- data
def test_drop_unclosed_bar():
    df = make_bars([(1, 1, 1, 1)] * 3, start="2024-01-01 00:00")
    now = pd.Timestamp("2024-01-01 09:00", tz="UTC")  # la vela de las 08:00 sigue abierta
    assert len(drop_unclosed(df, "4h", now)) == 2


def test_validate_detects_bad_candles():
    df = make_bars([(100, 99, 98, 100)])  # high < open
    with pytest.raises(ValueError):
        validate(df)


def test_max_drawdown():
    assert max_drawdown(pd.Series([100, 120, 90, 130])) == pytest.approx(0.25)
    assert max_drawdown(pd.Series(np.linspace(1, 2, 10))) == 0


# ---------------------------------------------------------------- portfolio
def test_config_symbols_and_legacy_symbol():
    assert AppConfig.model_validate({"market": {"symbol": "ETH/USDT"}}).market.symbols == [
        "ETH/USDT"
    ]
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"market": {"symbols": ["BTC/USDT", "BTC/USDT"]}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"market": {"symbols": ["BTC/USDT", "BTC/EUR"]}})


def test_portfolio_backtest_accounting_and_position_limit():
    from tradebot.backtest import run_portfolio_backtest, split_in_out_of_sample

    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    cfg = AppConfig.model_validate({"market": {"symbols": syms}, "risk": {"max_open_positions": 2}})
    data = {
        s: synthetic_ohlcv(3000, seed=i, start_price=50.0 * (i + 1)) for i, s in enumerate(syms)
    }
    res = run_portfolio_backtest(data, cfg)

    assert len({t.symbol for t in res.trades}) > 1
    pnl = sum(t.pnl for t in res.trades)
    assert res.equity.iloc[-1] == pytest.approx(cfg.backtest.initial_cash + pnl, rel=1e-9)
    assert sum(m["operaciones"] for m in res.per_symbol.values()) == len(res.trades)

    # Nunca hay más de 2 posiciones abiertas a la vez.
    events = sorted(
        [(t.entry_time, 1) for t in res.trades] + [(t.exit_time, -1) for t in res.trades],
        key=lambda e: (e[0], e[1]),  # a la misma hora, primero las salidas
    )
    open_now = peak = 0
    for _, delta in events:
        open_now += delta
        peak = max(peak, open_now)
    assert peak <= 2

    ins, oos = split_in_out_of_sample(data, 0.7)
    assert all(ins[s].index.max() < oos[s].index.min() for s in syms)
