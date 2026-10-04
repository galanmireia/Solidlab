"""Descarga de velas (OHLCV) con caché en CSV, y datos sintéticos para pruebas offline."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "volume"]


def timeframe_to_timedelta(timeframe: str) -> pd.Timedelta:
    return pd.Timedelta(ccxt.Exchange.parse_timeframe(timeframe), unit="s")


def periods_per_year(timeframe: str) -> float:
    return pd.Timedelta(days=365) / timeframe_to_timedelta(timeframe)


def ohlcv_to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", *COLUMNS])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    return df.astype(float)


def drop_unclosed(df: pd.DataFrame, timeframe: str, now: pd.Timestamp) -> pd.DataFrame:
    """Elimina la última vela si todavía no ha cerrado (operar con ella sería mirar al futuro)."""
    tf = timeframe_to_timedelta(timeframe)
    return df[df.index + tf <= now]


def validate(df: pd.DataFrame) -> None:
    if df.empty:
        raise ValueError("No hay datos")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError("Índice de tiempo desordenado o duplicado")
    bad = (df["high"] < df[["open", "close"]].max(axis=1)) | (
        df["low"] > df[["open", "close"]].min(axis=1)
    )
    if bad.any():
        raise ValueError(f"{int(bad.sum())} velas con high/low incoherentes")
    if df[COLUMNS].isna().any().any():
        raise ValueError("Hay valores vacíos en los datos")


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: str | None,
    until: str | None = None,
    limit: int = 1000,
) -> pd.DataFrame:
    """Descarga paginada desde ``since`` hasta ``until`` (o ahora)."""
    since_ms = exchange.parse8601(f"{since}T00:00:00Z") if since else None
    until_ts = pd.Timestamp(until, tz="UTC") if until else pd.Timestamp.now(tz="UTC")
    tf_ms = int(timeframe_to_timedelta(timeframe).total_seconds() * 1000)
    rows: list[list] = []
    while True:
        batch = _with_retries(lambda s=since_ms: exchange.fetch_ohlcv(symbol, timeframe, s, limit))
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1][0]
        log.info("Descargadas %d velas (hasta %s)", len(rows), pd.Timestamp(last, unit="ms"))
        if last + tf_ms > until_ts.value // 1_000_000 or len(batch) < 2:
            break
        since_ms = last + tf_ms
    df = ohlcv_to_frame(rows)
    df = df[df.index <= until_ts]
    return drop_unclosed(df, timeframe, pd.Timestamp.now(tz="UTC"))


def _with_retries(fn, attempts: int = 5):
    for i in range(attempts):
        try:
            return fn()
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            if i == attempts - 1:
                raise
            wait = 2**i
            log.warning("Error de red (%s); reintento en %ss", exc, wait)
            time.sleep(wait)
    return None


def load_or_fetch(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: str | None,
    until: str | None,
    data_dir: Path,
    refresh: bool = False,
) -> pd.DataFrame:
    data_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("/", "-")
    path = data_dir / f"{exchange.id}_{safe}_{timeframe}_{since or 'all'}_{until or 'now'}.csv"
    if path.exists() and not refresh and until:
        df = pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"])
    else:
        df = fetch_ohlcv(exchange, symbol, timeframe, since, until)
        df.to_csv(path)
    validate(df)
    return df


def synthetic_ohlcv(
    n: int = 5000,
    timeframe: str = "4h",
    seed: int = 42,
    start_price: float = 30_000.0,
    start: str = "2021-01-01",
) -> pd.DataFrame:
    """Precios simulados con regímenes alcistas/bajistas. SOLO para pruebas del software:
    un resultado bueno aquí no dice nada sobre el mercado real."""
    rng = np.random.default_rng(seed)
    regime_drift = np.repeat(rng.normal(0, 0.002, n // 250 + 1), 250)[:n]
    vol = 0.012 * np.exp(rng.normal(0, 0.25, n))
    rets = regime_drift + vol * rng.standard_t(4, n) / np.sqrt(2)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]]) * np.exp(rng.normal(0, 0.001, n))
    wick = np.abs(rng.normal(0, 0.006, (2, n)))
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])
    idx = pd.date_range(start, periods=n, freq=timeframe_to_timedelta(timeframe), tz="UTC")
    idx.name = "timestamp"
    volume = rng.lognormal(5, 0.5, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )
