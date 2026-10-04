from __future__ import annotations

import pandas as pd
import pytest

from tradebot.config import AppConfig


def make_bars(rows: list[tuple[float, float, float, float]], start="2024-01-01", freq="4h"):
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC", name="timestamp")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    return df


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig.model_validate({"costs": {"fee_rate": 0.001, "slippage_bps": 0}})
