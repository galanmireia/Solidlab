"""Backtest vela a vela con el mismo ``Trader`` que se usa en vivo.

Orden de eventos en cada vela t (evita mirar al futuro):
  apertura  -> se ejecuta la orden decidida al cierre de t-1
  durante   -> se comprueba el stop con el mínimo de la vela
  cierre    -> la estrategia decide para la vela siguiente
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradebot.brokers.simulated import SimulatedBroker
from tradebot.config import AppConfig
from tradebot.data import periods_per_year
from tradebot.journal import Journal
from tradebot.metrics import compute_metrics
from tradebot.models import Fill, Trade
from tradebot.risk import RiskManager
from tradebot.strategies import build_strategy
from tradebot.strategies.base import Strategy
from tradebot.trader import Trader


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list[Trade]
    fills: list[Fill]
    metrics: dict[str, float]
    halted: bool = False
    halt_reason: str = ""
    params: dict = field(default_factory=dict)


def run_backtest(
    df: pd.DataFrame,
    cfg: AppConfig,
    strategy: Strategy | None = None,
    journal: Journal | None = None,
) -> BacktestResult:
    strategy = strategy or build_strategy(cfg.strategy.name, cfg.strategy.params)
    broker = SimulatedBroker(
        cfg.market.symbol, cfg.backtest.initial_cash, cfg.costs.fee_rate, cfg.costs.slippage_bps
    )
    trader = Trader(cfg.market.symbol, strategy, RiskManager(cfg.risk), broker, journal)

    data = strategy.prepare(df)
    equity = []
    in_market = 0

    for ts, bar in data.iterrows():
        trader.execute_pending(float(bar["open"]), ts)
        trader.check_stop(float(bar["low"]), ts, open_price=float(bar["open"]))
        trader.on_bar_close(bar)
        equity.append(trader.equity(float(bar["close"])))
        in_market += trader.position is not None

    # Cierra la posición abierta al final para que las métricas sean completas.
    if trader.position is not None:
        last = data.iloc[-1]
        trader.close_position(float(last["close"]), last.name, "fin del backtest")
        equity[-1] = trader.equity(float(last["close"]))

    equity_s = pd.Series(equity, index=data.index, name="equity")
    metrics = compute_metrics(
        equity_s,
        trader.trades,
        periods_per_year(cfg.market.timeframe),
        benchmark_close=data["close"],
        exposure=in_market / len(data) if len(data) else 0.0,
    )
    return BacktestResult(
        equity_s,
        trader.trades,
        trader.fills,
        metrics,
        trader.risk.state.halted,
        trader.risk.state.halt_reason,
        dict(strategy.params),
    )


def split_in_out_of_sample(
    df: pd.DataFrame, train_frac: float = 0.7
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Divide en periodo de ajuste (in-sample) y periodo de validación (out-of-sample).

    Cada tramo recalcula los indicadores desde cero, así que sus primeras
    ``strategy.warmup`` velas no generan señales.
    """
    cut = int(len(df) * train_frac)
    return df.iloc[:cut], df.iloc[cut:]
