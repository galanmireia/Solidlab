"""Datos de bolsa (acciones y ETFs) desde Yahoo Finance, con la misma interfaz que ccxt.

Solo sirve para datos: backtest y paper trading. No envía órdenes. Para operar
acciones con dinero real haría falta un bróker con API (p. ej. Interactive Brokers).

Notas:
- Los símbolos son los de Yahoo: ``SPY``, ``AAPL``, ``SAN.MC`` (Bolsa de Madrid)...
- Los precios están ajustados por splits y dividendos para que los indicadores
  no vean saltos falsos.
- El mercado solo abre unas horas al día: fuera de horario el "precio actual"
  es el último cierre.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import ccxt
import pandas as pd

log = logging.getLogger(__name__)

# Intervalos de Yahoo equivalentes a los de ccxt.
_INTERVALS = {"1h": "1h", "1d": "1d", "1w": "1wk"}


def _yf():
    try:
        import yfinance
    except ImportError as exc:  # pragma: no cover - dependencia declarada en pyproject
        raise RuntimeError("Falta la librería yfinance: pip install yfinance") from exc
    return yfinance


class YahooMarket:
    """Imita los métodos de ``ccxt.Exchange`` que usa el bot (solo lectura)."""

    id = "yahoo"

    def __init__(self, yf_module=None, clock: Callable[[], pd.Timestamp] | None = None) -> None:
        self.yf = yf_module or _yf()
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))

    # ccxt.Exchange.parse8601 es de instancia; lo reproducimos para data.fetch_ohlcv
    @staticmethod
    def parse8601(text: str) -> int:
        return int(pd.Timestamp(text).value // 1_000_000)

    def _interval(self, timeframe: str) -> str:
        try:
            return _INTERVALS[timeframe]
        except KeyError:
            raise ValueError(
                f"Timeframe '{timeframe}' no soportado para bolsa; usa {sorted(_INTERVALS)}"
            ) from None

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[float]]:
        interval = self._interval(timeframe)
        ticker = self.yf.Ticker(symbol)
        if since is not None:
            start = pd.Timestamp(since, unit="ms", tz="UTC")
        else:
            # Suficientes velas para calentar los indicadores: la bolsa abre ~5 de 7 días
            # y ~7 horas al día, así que se pide de más y luego se recorta a ``limit``.
            n = limit or 300
            span = {"1d": pd.Timedelta(days=n * 1.6), "1wk": pd.Timedelta(weeks=n * 1.1)}
            # 1h: ~6,5 velas por día hábil ≈ 4,6 por día natural; Yahoo limita a 730 días.
            lookback = span.get(interval, pd.Timedelta(days=min(n / 4 + 7, 720)))
            start = self.clock() - lookback - pd.Timedelta(days=5)
        try:
            df = ticker.history(
                start=start.strftime("%Y-%m-%d"), interval=interval, auto_adjust=True
            )
        except Exception as exc:  # noqa: BLE001 - yfinance lanza tipos variados
            raise ccxt.NetworkError(f"Yahoo Finance falló para {symbol}: {exc}") from exc
        if df is None or df.empty:
            # yfinance devuelve vacío tanto si el símbolo no existe como si Yahoo falla
            # puntualmente: se trata como error temporal (se reintenta y luego se avisa).
            raise ccxt.NetworkError(
                f"Yahoo Finance no devuelve datos para '{symbol}' "
                "(¿símbolo mal escrito o Yahoo caído?)"
            )

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        # El ajuste por dividendos redondea cada columna por separado y a veces deja el
        # máximo unas milésimas por debajo del cierre (o el mínimo por encima): se corrige.
        df = df.assign(
            High=df[["Open", "High", "Close"]].max(axis=1),
            Low=df[["Open", "Low", "Close"]].min(axis=1),
        )
        idx = df.index
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        if since is not None:
            keep = idx >= pd.Timestamp(since, unit="ms", tz="UTC")
            df, idx = df[keep], idx[keep]
        rows = [
            [int(ts.value // 1_000_000), float(o), float(h), float(lo), float(c), float(v)]
            for ts, (o, h, lo, c, v) in zip(idx, df.itertuples(index=False), strict=True)
        ]
        return rows[-limit:] if limit and since is None else rows

    def fetch_ticker(self, symbol: str) -> dict:
        try:
            price = self.yf.Ticker(symbol).fast_info["last_price"]
        except Exception as exc:  # noqa: BLE001
            raise ccxt.NetworkError(f"Yahoo Finance falló para {symbol}: {exc}") from exc
        return {"symbol": symbol, "last": float(price) if price else None}
