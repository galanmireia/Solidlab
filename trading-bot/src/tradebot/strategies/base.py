from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from tradebot.models import Position, Signal


class Strategy(ABC):
    """Una estrategia solo decide QUÉ hacer; el tamaño y la ejecución son cosa del Trader.

    - ``prepare`` añade columnas de indicadores de forma vectorizada (y causal).
    - ``on_bar`` recibe la última vela CERRADA y la posición actual.
    """

    name: str = "base"

    def __init__(self, **params: Any) -> None:
        unknown = set(params) - set(self.default_params())
        if unknown:
            raise ValueError(f"Parámetros desconocidos para {self.name}: {sorted(unknown)}")
        self.params: dict[str, Any] = {**self.default_params(), **params}

    @classmethod
    @abstractmethod
    def default_params(cls) -> dict[str, Any]: ...

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Velas necesarias antes de que los indicadores sean válidos."""

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame: ...

    @abstractmethod
    def on_bar(self, bar: pd.Series, position: Position | None) -> Signal: ...
