"""Indicadores técnicos. Todos son causales: el valor en la vela t solo usa datos <= t."""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), period)
    loss = _wilder(-delta.clip(upper=0), period)
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    # Sin pérdidas en la ventana -> RSI 100; sin movimiento -> 50.
    out = out.where(loss != 0, 100.0)
    out = out.where((gain != 0) | (loss != 0), 50.0)
    return out.where(gain.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def donchian_high(high: pd.Series, period: int) -> pd.Series:
    """Máximo de las ``period`` velas ANTERIORES (excluye la actual para detectar rupturas)."""
    return high.shift(1).rolling(period, min_periods=period).max()
