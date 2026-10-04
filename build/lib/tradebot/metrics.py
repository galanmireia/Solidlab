"""Métricas de rendimiento de un backtest."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from tradebot.models import Trade


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((1 - equity / peak).max()) if len(equity) else 0.0


def _profit_factor(wins: list[float], losses: list[float]) -> float:
    gross_loss = -sum(losses)
    if gross_loss > 0:
        return sum(wins) / gross_loss
    return float("inf") if wins else 0.0


def compute_metrics(
    equity: pd.Series,
    trades: list[Trade],
    bars_per_year: float,
    benchmark_close: pd.Series | None = None,
    exposure: float | None = None,
) -> dict[str, float]:
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    years = len(equity) / bars_per_year
    rets = equity.pct_change().dropna()
    vol = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    downside = rets[rets < 0]
    dd = max_drawdown(equity)
    cagr = (end / start) ** (1 / years) - 1 if years > 0 and end > 0 else float("nan")

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    m: dict[str, float] = {
        "capital_inicial": start,
        "capital_final": end,
        "rentabilidad_total": end / start - 1,
        "rentabilidad_anual_cagr": cagr,
        "volatilidad_anual": vol * math.sqrt(bars_per_year),
        "sharpe": (float(rets.mean()) / vol * math.sqrt(bars_per_year)) if vol > 0 else 0.0,
        "sortino": (
            float(rets.mean()) / float(downside.std(ddof=1)) * math.sqrt(bars_per_year)
            if len(downside) > 1 and downside.std(ddof=1) > 0
            else 0.0
        ),
        "max_drawdown": dd,
        "calmar": cagr / dd if dd > 0 and not math.isnan(cagr) else 0.0,
        "num_operaciones": len(trades),
        "tasa_acierto": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": _profit_factor(wins, losses),
        "ganancia_media": float(np.mean(wins)) if wins else 0.0,
        "perdida_media": float(np.mean(losses)) if losses else 0.0,
        "esperanza_por_operacion": float(np.mean([t.pnl for t in trades])) if trades else 0.0,
        "comisiones_totales": sum(t.fees for t in trades),
    }
    if exposure is not None:
        m["tiempo_invertido"] = exposure
    if benchmark_close is not None and len(benchmark_close) > 1:
        bh = benchmark_close / benchmark_close.iloc[0]
        m["buy_and_hold_rentabilidad"] = float(bh.iloc[-1] - 1)
        m["buy_and_hold_max_drawdown"] = max_drawdown(bh)
    return m


_PCT = {
    "rentabilidad_total",
    "rentabilidad_anual_cagr",
    "volatilidad_anual",
    "max_drawdown",
    "tasa_acierto",
    "tiempo_invertido",
    "buy_and_hold_rentabilidad",
    "buy_and_hold_max_drawdown",
}


def format_metrics(m: dict[str, float]) -> str:
    lines = []
    for k, v in m.items():
        label = k.replace("_", " ").capitalize()
        if k in _PCT:
            val = f"{v:.2%}"
        elif k == "num_operaciones":
            val = f"{int(v)}"
        else:
            val = f"{v:,.2f}"
        lines.append(f"  {label:<30} {val:>14}")
    return "\n".join(lines)
