"""Configuración validada del bot.

Todos los valores tienen límites razonables: un error de tipeo en el YAML
(por ejemplo ``risk_per_trade: 1`` en vez de ``0.01``) se rechaza al arrancar
en lugar de arriesgar el 100 % de la cuenta en una operación.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExchangeConfig(_Strict):
    id: str = "binance"
    testnet: bool = True


class MarketConfig(_Strict):
    symbol: str = "BTC/USDT"
    timeframe: str = "4h"


class StrategyConfig(_Strict):
    name: str = "ema_trend"
    params: dict[str, Any] = Field(default_factory=dict)


class RiskConfig(_Strict):
    # Fracción del capital que se pierde si salta el stop (0.01 = 1 %).
    risk_per_trade: float = Field(0.01, gt=0, le=0.05)
    # Tamaño máximo de una posición como fracción del capital (1.0 = sin apalancamiento).
    max_position_pct: float = Field(0.5, gt=0, le=1.0)
    # Si en el día UTC se pierde esta fracción, no se abren más operaciones hasta mañana.
    max_daily_loss_pct: float = Field(0.03, gt=0, le=0.5)
    # Si la caída desde el máximo de capital alcanza esta fracción, el bot se detiene del todo.
    max_drawdown_pct: float = Field(0.15, gt=0, le=0.9)
    # Valor mínimo de una orden en moneda de cotización (evita órdenes "polvo").
    min_order_notional: float = Field(10.0, ge=0)


class CostsConfig(_Strict):
    # Comisión por lado (0.001 = 0.1 %, típico de Binance spot sin descuentos).
    fee_rate: float = Field(0.001, ge=0, le=0.01)
    # Deslizamiento simulado en puntos básicos (5 = 0.05 %).
    slippage_bps: float = Field(5.0, ge=0, le=200)


class BacktestConfig(_Strict):
    initial_cash: float = Field(10_000.0, gt=0)
    start: str | None = "2021-01-01"
    end: str | None = None


class PaperConfig(_Strict):
    initial_cash: float = Field(10_000.0, gt=0)
    poll_seconds: int = Field(30, ge=5, le=3600)


class LiveConfig(_Strict):
    # Doble seguro: además de esto hace falta TRADEBOT_LIVE_CONFIRM=YES y confirmar por teclado.
    enabled: bool = False
    poll_seconds: int = Field(30, ge=5, le=3600)


class AppConfig(_Strict):
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    market: MarketConfig = Field(default_factory=MarketConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    data_dir: Path = Path("data")
    state_dir: Path = Path("state")
    journal_dir: Path = Path("journal")
    log_dir: Path = Path("logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def _check_stop_vs_size(self) -> AppConfig:
        if self.risk.max_daily_loss_pct < self.risk.risk_per_trade:
            raise ValueError("max_daily_loss_pct debe ser >= risk_per_trade")
        return self


def load_config(path: str | Path | None) -> AppConfig:
    if path is None:
        return AppConfig()
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return AppConfig.model_validate(raw)
