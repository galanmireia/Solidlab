"""Seguimiento de tendencia: cruce de medias exponenciales con filtro y stop ATR.

- Entrada: la EMA rápida cruza por encima de la lenta y el precio está sobre la EMA
  de tendencia (solo compramos en mercado alcista).
- Stop inicial: ``close - atr_mult * ATR``. Después sube como "chandelier stop".
- Salida: la EMA rápida cruza por debajo de la lenta, o salta el stop.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradebot.indicators import atr, ema
from tradebot.models import Action, Position, Signal
from tradebot.strategies.base import Strategy


class EmaTrend(Strategy):
    name = "ema_trend"

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {"fast": 20, "slow": 50, "trend": 200, "atr_period": 14, "atr_mult": 3.0}

    @property
    def warmup(self) -> int:
        p = self.params
        return max(p["slow"], p["trend"], p["atr_period"]) + 1

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = df.copy()
        out["ema_fast"] = ema(out["close"], p["fast"])
        out["ema_slow"] = ema(out["close"], p["slow"])
        out["ema_trend"] = ema(out["close"], p["trend"])
        out["atr"] = atr(out, p["atr_period"])
        above = out["ema_fast"] > out["ema_slow"]
        prev_above = above.shift(1, fill_value=False)
        out["cross_up"] = above & ~prev_above
        out["cross_down"] = ~above & prev_above
        return out

    def on_bar(self, bar: pd.Series, position: Position | None) -> Signal:
        if pd.isna(bar["ema_trend"]) or pd.isna(bar["atr"]):
            return Signal.hold()
        stop = float(bar["close"] - self.params["atr_mult"] * bar["atr"])

        if position is None:
            if bar["cross_up"] and bar["close"] > bar["ema_trend"]:
                return Signal(Action.ENTER, stop, "cruce alcista EMA")
            return Signal.hold()

        if bar["cross_down"]:
            return Signal(Action.EXIT, reason="cruce bajista EMA")
        return Signal.hold(stop)
