"""Reversión a la media: compra caídas fuertes dentro de una tendencia alcista.

- Entrada: RSI por debajo de ``rsi_entry`` y precio sobre la SMA de tendencia.
- Stop: ``close - atr_mult * ATR`` (fijo, no trailing: es una apuesta a rebote corto).
- Salida: RSI por encima de ``rsi_exit`` o ``max_bars`` velas sin rebote.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.indicators import atr, rsi, sma
from tradebot.models import Action, Position, Signal
from tradebot.strategies.base import Strategy


class RsiReversion(Strategy):
    name = "rsi_reversion"

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "rsi_period": 14,
            "rsi_entry": 30.0,
            "rsi_exit": 55.0,
            "trend": 200,
            "atr_period": 14,
            "atr_mult": 2.5,
            "max_bars": 20,
        }

    @property
    def warmup(self) -> int:
        p = self.params
        return max(p["trend"], p["rsi_period"], p["atr_period"]) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = df.copy()
        out["rsi"] = rsi(out["close"], p["rsi_period"])
        out["sma_trend"] = sma(out["close"], p["trend"])
        out["atr"] = atr(out, p["atr_period"])
        return out

    def on_bar(self, bar: pd.Series, position: Position | None) -> Signal:
        if pd.isna(bar["sma_trend"]) or pd.isna(bar["rsi"]) or pd.isna(bar["atr"]):
            return Signal.hold()
        p = self.params

        if position is None:
            if bar["rsi"] < p["rsi_entry"] and bar["close"] > bar["sma_trend"]:
                stop = float(bar["close"] - p["atr_mult"] * bar["atr"])
                return Signal(Action.ENTER, stop, f"RSI {bar['rsi']:.1f} en tendencia alcista")
            return Signal.hold()

        if bar["rsi"] > p["rsi_exit"]:
            return Signal(Action.EXIT, reason=f"RSI {bar['rsi']:.1f} recuperado")
        if position.bars_held >= p["max_bars"]:
            return Signal(Action.EXIT, reason="tiempo máximo sin rebote")
        return Signal.hold()
