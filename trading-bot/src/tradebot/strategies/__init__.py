from tradebot.strategies.base import Strategy
from tradebot.strategies.ema_trend import EmaTrend
from tradebot.strategies.rsi_reversion import RsiReversion

REGISTRY: dict[str, type[Strategy]] = {cls.name: cls for cls in (EmaTrend, RsiReversion)}


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Estrategia desconocida '{name}'. Disponibles: {sorted(REGISTRY)}"
        ) from None
    return cls(**(params or {}))


__all__ = ["Strategy", "EmaTrend", "RsiReversion", "REGISTRY", "build_strategy"]
