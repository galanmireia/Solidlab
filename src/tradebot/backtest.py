"""Backtest vela a vela con el mismo ``Trader``/``Portfolio`` que se usa en vivo.

Orden de eventos en cada instante t, para todas las monedas (evita mirar al futuro):
  apertura  -> se ejecutan las órdenes decididas al cierre de t-1
  durante   -> se comprueban los stops con el mínimo de la vela
  cierre    -> se valoran las posiciones y cada estrategia decide para la vela siguiente
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradebot.brokers.simulated import PaperAccount, SimulatedBroker
from tradebot.config import AppConfig
from tradebot.data import periods_per_year
from tradebot.journal import Journal
from tradebot.metrics import compute_metrics
from tradebot.models import Fill, Trade
from tradebot.portfolio import Portfolio
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
    per_symbol: dict[str, dict[str, float]] = field(default_factory=dict)


def build_paper_portfolio(
    cfg: AppConfig,
    symbols: list[str],
    initial_cash: float,
    strategies: dict[str, Strategy] | None = None,
    journal: Journal | None = None,
) -> tuple[Portfolio, PaperAccount]:
    account = PaperAccount(initial_cash)
    traders = []
    for sym in symbols:
        strategy = (strategies or {}).get(sym) or build_strategy(
            cfg.strategy.name, cfg.strategy.params
        )
        broker = SimulatedBroker(
            sym, None, cfg.costs.fee_rate, cfg.costs.slippage_bps, account=account
        )
        traders.append(Trader(sym, strategy, RiskManager(cfg.risk), broker, journal))
    portfolio = Portfolio(traders, RiskManager(cfg.risk), cfg.risk.max_open_positions)
    return portfolio, account


def run_portfolio_backtest(
    data: dict[str, pd.DataFrame],
    cfg: AppConfig,
    strategies: dict[str, Strategy] | None = None,
    journal: Journal | None = None,
) -> BacktestResult:
    symbols = list(data)
    portfolio, _ = build_paper_portfolio(
        cfg, symbols, cfg.backtest.initial_cash, strategies, journal
    )
    traders = portfolio.traders

    # Filas preparadas por instante: {timestamp: {símbolo: vela}}
    prepared = {s: traders[s].strategy.prepare(df) for s, df in data.items()}
    rows: dict[pd.Timestamp, dict[str, pd.Series]] = {}
    for sym, df in prepared.items():
        for ts, bar in df.iterrows():
            rows.setdefault(ts, {})[sym] = bar
    index = sorted(rows)

    equity: list[float] = []
    in_market = 0
    for ts in index:
        bars = rows[ts]
        for sym, bar in bars.items():
            t = traders[sym]
            t.execute_pending(float(bar["open"]), ts)
            t.check_stop(float(bar["low"]), ts, open_price=float(bar["open"]))
        for sym, bar in bars.items():
            portfolio.mark(sym, float(bar["close"]))
        for sym, bar in bars.items():
            traders[sym].on_bar_close(bar)
        equity.append(portfolio.equity())
        in_market += any(t.position for t in traders.values())

    # Cierra lo abierto al final para que las métricas estén completas.
    if index:
        for sym, t in traders.items():
            if t.position is not None:
                last = prepared[sym].iloc[-1]
                t.close_position(float(last["close"]), last.name, "fin del backtest")
        equity[-1] = portfolio.equity()

    equity_s = pd.Series(equity, index=pd.DatetimeIndex(index, name="timestamp"), name="equity")
    trades = portfolio.trades()
    metrics = compute_metrics(
        equity_s,
        trades,
        periods_per_year(cfg.market.timeframe),
        benchmark_close=equal_weight_benchmark(data),
        exposure=in_market / len(index) if index else 0.0,
    )
    per_symbol = {}
    for sym in symbols:
        st = [tr for tr in trades if tr.symbol == sym]
        per_symbol[sym] = {
            "operaciones": len(st),
            "resultado": sum(tr.pnl for tr in st),
            "aciertos": (sum(tr.pnl > 0 for tr in st) / len(st)) if st else 0.0,
        }
    first = next(iter(traders.values()))
    return BacktestResult(
        equity_s,
        trades,
        sorted((f for t in traders.values() for f in t.fills), key=lambda f: f.timestamp),
        metrics,
        portfolio.risk.state.halted,
        portfolio.risk.state.halt_reason,
        dict(first.strategy.params),
        per_symbol,
    )


def run_backtest(
    df: pd.DataFrame,
    cfg: AppConfig,
    strategy: Strategy | None = None,
    journal: Journal | None = None,
) -> BacktestResult:
    """Backtest de una sola moneda (la primera de la configuración)."""
    sym = cfg.market.symbols[0]
    return run_portfolio_backtest({sym: df}, cfg, {sym: strategy} if strategy else None, journal)


def equal_weight_benchmark(data: dict[str, pd.DataFrame]) -> pd.Series:
    """Comprar a partes iguales todas las monedas al inicio y no tocar nada."""
    closes = pd.DataFrame({s: df["close"] for s, df in data.items()}).sort_index().ffill()
    closes = closes.dropna()
    if closes.empty:
        return pd.Series(dtype=float)
    return (closes / closes.iloc[0]).mean(axis=1)


def split_in_out_of_sample(
    data: dict[str, pd.DataFrame], train_frac: float = 0.7
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Divide todas las monedas por la MISMA fecha en ajuste (in-sample) y validación.

    Cada tramo recalcula los indicadores desde cero, así que sus primeras
    ``strategy.warmup`` velas no generan señales.
    """
    index = sorted(set().union(*(df.index for df in data.values())))
    cut = index[int(len(index) * train_frac)]
    ins = {s: df[df.index < cut] for s, df in data.items()}
    oos = {s: df[df.index >= cut] for s, df in data.items()}
    return ins, oos
