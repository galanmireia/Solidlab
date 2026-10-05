"""Creación del cliente ccxt (el "conector" con el exchange)."""

from __future__ import annotations

import logging
import os

import ccxt

from tradebot.config import ExchangeConfig

log = logging.getLogger(__name__)


def make_exchange(cfg: ExchangeConfig, authenticated: bool = False) -> ccxt.Exchange:
    """Crea el cliente. Sin autenticar solo puede leer datos públicos (velas, precios).

    ``id: yahoo`` da datos de bolsa (acciones/ETFs) de Yahoo Finance: solo lectura.
    """
    if cfg.id == "yahoo":
        if authenticated:
            raise RuntimeError(
                "Yahoo Finance solo da datos: no se puede operar en real ni en testnet con él. "
                "Usa paper trading."
            )
        from tradebot.stocks import YahooMarket

        return YahooMarket()  # type: ignore[return-value]
    if not hasattr(ccxt, cfg.id):
        raise ValueError(f"Exchange '{cfg.id}' no soportado por ccxt")
    options: dict = {"enableRateLimit": True, "options": {"defaultType": "spot"}}

    if authenticated:
        key = os.getenv("TRADEBOT_API_KEY", "")
        secret = os.getenv("TRADEBOT_API_SECRET", "")
        if not key or not secret:
            raise RuntimeError("Faltan TRADEBOT_API_KEY / TRADEBOT_API_SECRET (ver .env.example)")
        options.update(apiKey=key, secret=secret)
        if password := os.getenv("TRADEBOT_API_PASSWORD"):
            options["password"] = password

    exchange: ccxt.Exchange = getattr(ccxt, cfg.id)(options)
    if cfg.testnet:
        # Mejor fallar que acabar operando en real por un exchange sin testnet.
        if not exchange.urls.get("test"):
            raise ccxt.NotSupported(f"{cfg.id} no tiene testnet en ccxt; usa paper trading")
        exchange.set_sandbox_mode(True)
        log.info("Usando TESTNET de %s", cfg.id)
    return exchange
