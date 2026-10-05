from __future__ import annotations

import ccxt
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from test_core import ScriptedStrategy

from tradebot.backtest import run_portfolio_backtest
from tradebot.brokers.simulated import PaperAccount, SimulatedBroker
from tradebot.cli import main
from tradebot.commands import Book, make_handler
from tradebot.config import AppConfig, ExchangeConfig, RiskConfig
from tradebot.data import drop_unclosed, ohlcv_to_frame
from tradebot.exchange import make_exchange
from tradebot.models import Action, Signal
from tradebot.portfolio import Portfolio
from tradebot.risk import RiskManager
from tradebot.runner import LiveRunner
from tradebot.stocks import YahooMarket
from tradebot.trader import Trader


def ny_daily(start: str, end: str, price: float = 100.0) -> pd.DataFrame:
    """Velas diarias como las da yfinance: solo días hábiles, índice a medianoche de Nueva York."""
    days = pd.bdate_range(start, end, tz="America/New_York")
    close = price * np.exp(np.cumsum(np.full(len(days), 0.001)))
    return pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1e6,
            "Dividends": 0.0,
            "Stock Splits": 0.0,
        },
        index=days,
    )


class FakeYF:
    """Imita el módulo yfinance."""

    def __init__(self, frames: dict[str, pd.DataFrame], last: dict[str, float] | None = None):
        self.frames, self.last, self.calls = frames, last or {}, []

    def Ticker(self, symbol):  # noqa: N802 - mismo nombre que yfinance
        yf = self

        class _T:
            @property
            def fast_info(self):
                return {"last_price": yf.last.get(symbol, 100.0)}

            def history(self, start=None, interval="1d", auto_adjust=True, **_):
                yf.calls.append((symbol, start, interval, auto_adjust))
                if symbol not in yf.frames:
                    return pd.DataFrame()
                df = yf.frames[symbol]
                return df[df.index >= pd.Timestamp(start, tz="America/New_York")]

        return _T()


def test_yahoo_ohlcv_matches_ccxt_format():
    yf = FakeYF({"SPY": ny_daily("2024-01-01", "2024-03-29")})
    market = YahooMarket(yf, clock=lambda: pd.Timestamp("2024-03-30", tz="UTC"))
    since = market.parse8601("2024-02-01T00:00:00Z")
    rows = market.fetch_ohlcv("SPY", "1d", since=since)
    df = ohlcv_to_frame(rows)
    assert df.index.tz is not None and str(df.index.tz) == "UTC"
    assert df.index[0] >= pd.Timestamp("2024-02-01", tz="UTC")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert yf.calls[-1][3] is True  # precios ajustados por splits/dividendos

    tail = market.fetch_ohlcv("SPY", "1d", limit=10)
    assert len(tail) == 10
    assert market.fetch_ticker("SPY")["last"] == 100.0


def test_yahoo_errors_are_clear():
    market = YahooMarket(FakeYF({}))
    with pytest.raises(ccxt.NetworkError, match="símbolo mal escrito"):
        market.fetch_ohlcv("NOEXISTE", "1d", limit=5)
    with pytest.raises(ValueError):
        market.fetch_ohlcv("SPY", "4h", limit=5)  # Yahoo no tiene velas de 4h
    with pytest.raises(RuntimeError, match="solo da datos"):
        make_exchange(ExchangeConfig(id="yahoo", testnet=False), authenticated=True)


def test_today_unfinished_daily_candle_is_ignored():
    yf = FakeYF({"SPY": ny_daily("2024-03-01", "2024-03-15")})
    now = pd.Timestamp("2024-03-15 18:00", tz="UTC")
    rows = YahooMarket(yf, clock=lambda: now).fetch_ohlcv("SPY", "1d", limit=50)
    # Viernes 15/03 a las 18:00 UTC: la bolsa aún está abierta, esa vela no cuenta.
    df = drop_unclosed(ohlcv_to_frame(rows), "1d", now)
    assert df.index[-1].date() == pd.Timestamp("2024-03-14").date()


def test_stock_config_rules():
    ok = AppConfig.model_validate({"market": {"symbols": ["SPY", "AAPL", "SAN.MC"]}})
    assert ok.market.symbols[0] == "SPY"
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"market": {"symbols": ["SPY", "BTC/USDT"]}})


def test_stock_backtest_with_daily_bars():
    cfg = AppConfig.model_validate(
        {"name": "Bolsa", "market": {"symbols": ["SPY", "QQQ"], "timeframe": "1d"}}
    )
    rng = np.random.default_rng(1)
    data = {}
    for i, sym in enumerate(cfg.market.symbols):
        days = pd.bdate_range("2018-01-01", "2023-12-29", tz="UTC", name="timestamp")
        close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, len(days)) + 0.0002 * i))
        data[sym] = pd.DataFrame(
            {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1},
            index=days,
        )
    res = run_portfolio_backtest(data, cfg)
    assert len(res.trades) > 0
    pnl = sum(t.pnl for t in res.trades)
    assert res.equity.iloc[-1] == pytest.approx(cfg.backtest.initial_cash + pnl, rel=1e-9)


def _stock_runner(tmp_path, yf, now_ref):
    account = PaperAccount(10_000)
    trader = Trader(
        "SPY",
        ScriptedStrategy({0: Signal(Action.ENTER, 50, "in")}),
        RiskManager(RiskConfig()),
        SimulatedBroker("SPY", None, 0.0005, 0, account=account),
    )
    pf = Portfolio([trader], RiskManager(RiskConfig()), 3)
    return LiveRunner(
        pf,
        YahooMarket(yf, clock=lambda: now_ref[0]),
        "1d",
        tmp_path / "s.json",
        120,
        clock=lambda: now_ref[0],
        sleep=lambda s: None,
    )


def test_weekend_does_not_hammer_yahoo_and_monday_trades(tmp_path):
    yf = FakeYF({"SPY": ny_daily("2023-06-01", "2024-03-15")})  # último día: viernes 15/03
    now = [pd.Timestamp("2024-03-16 12:00", tz="UTC")]  # sábado
    runner = _stock_runner(tmp_path, yf, now)
    runner.bootstrap()
    last = runner.last_bar_ts["SPY"]
    assert last.tz_convert("America/New_York").date() == pd.Timestamp("2024-03-15").date()

    # Domingo: "toca" vela nueva, pero no hay; tras una consulta, espera antes de repetir.
    now[0] = pd.Timestamp("2024-03-17 12:00", tz="UTC")
    runner.step()
    n = len(yf.calls)
    for minutes in (1, 2, 3):
        now[0] = pd.Timestamp("2024-03-17 12:00", tz="UTC") + pd.Timedelta(minutes=minutes)
        runner.step()
    assert len(yf.calls) == n  # sin descargas repetidas el fin de semana
    assert runner.portfolio.traders["SPY"].position is None

    # Martes de madrugada: la vela del lunes ya está cerrada y la estrategia actúa.
    yf.frames["SPY"] = ny_daily("2023-06-01", "2024-03-18")
    now[0] = pd.Timestamp("2024-03-19 06:00", tz="UTC")
    runner.step()
    assert runner.portfolio.traders["SPY"].position is not None


def test_telegram_shows_several_portfolios(tmp_path):
    from test_runner import FakeExchange, make_runner

    ex = FakeExchange(pd.Timestamp("2024-01-01", tz="UTC"))
    ex.now += pd.Timedelta(hours=9)
    crypto = make_runner(tmp_path, {}, ex, state="c.json")
    crypto.bootstrap()
    yf = FakeYF({"SPY": ny_daily("2023-06-01", "2024-03-15")})
    stocks = _stock_runner(tmp_path, yf, [pd.Timestamp("2024-03-16 12:00", tz="UTC")])
    stocks.bootstrap()

    handle = make_handler([Book("Cripto", crypto, 10_000), Book("Bolsa", stocks, 10_000)])
    status = handle("/estado")
    assert "Cripto" in status and "Bolsa" in status
    assert "SPY" in handle("/precios") and "BTC" in handle("/precios")
    handle("/pausa")
    assert crypto.portfolio.risk.state.paused and stocks.portfolio.risk.state.paused


def test_cli_rejects_duplicate_portfolio_names(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text(f"name: X\nlog_dir: {tmp_path}\n")
    with pytest.raises(SystemExit):
        main(["-c", str(a), "-c", str(a), "status"])


def test_hourly_history_is_long_enough_for_indicators():
    now = pd.Timestamp("2024-03-15 21:00", tz="UTC")
    hours = pd.date_range(pd.Timestamp("2023-01-01", tz="UTC"), now, freq="1h")
    hours = hours[(hours.dayofweek < 5) & (hours.hour >= 14) & (hours.hour < 21)]  # ~horario NY
    df = pd.DataFrame({c: 100.0 for c in ["Open", "High", "Low", "Close", "Volume"]}, index=hours)
    yf = FakeYF({"SPY": df})
    rows = YahooMarket(yf, clock=lambda: now).fetch_ohlcv("SPY", "1h", limit=251)
    assert len(rows) == 251


def test_yahoo_rounding_glitches_are_repaired():
    df = ny_daily("2024-01-01", "2024-01-31")
    df.iloc[3, df.columns.get_loc("High")] = df["Close"].iloc[3] - 0.001  # como el bug real
    df.iloc[5, df.columns.get_loc("Low")] = df["Close"].iloc[5] + 0.001
    now = pd.Timestamp("2024-02-01", tz="UTC")
    rows = YahooMarket(FakeYF({"SPY": df}), clock=lambda: now).fetch_ohlcv("SPY", "1d", limit=50)
    from tradebot.data import validate

    validate(ohlcv_to_frame(rows))  # no lanza
