from __future__ import annotations

import ccxt
import pandas as pd
import pytest

from tradebot.brokers.ccxt_broker import CcxtBroker, OrderUncertainError
from tradebot.cli import main
from tradebot.config import ExchangeConfig
from tradebot.exchange import make_exchange

NOW = pd.Timestamp("2024-01-01", tz="UTC")


class FakeCcxt:
    """Imita la API de ccxt lo justo para probar el bróker."""

    def __init__(self, fee_ccy="BTC", fail_create=False):
        self.balance = {"USDT": 1_000.0, "BTC": 0.0}
        self.fee_ccy = fee_ccy
        self.fail_create = fail_create
        self.orders = []
        self.markets = {}

    def load_markets(self):
        self.markets = {"BTC/USDT": {}}

    def market(self, symbol):
        return {
            "base": "BTC",
            "quote": "USDT",
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 5}},
        }

    def fetch_balance(self):
        return {"free": dict(self.balance)}

    def amount_to_precision(self, symbol, qty):
        return f"{int(qty * 1000) / 1000:.3f}"

    def create_order(self, symbol, type_, side, qty, price, params):
        if self.fail_create:
            raise ccxt.NetworkError("timeout")
        self.orders.append((side, qty, params["clientOrderId"]))
        px = 100.0
        fee_cost = qty * 0.001 if self.fee_ccy == "BTC" else qty * px * 0.001
        if side == "buy":
            self.balance["USDT"] -= qty * px
            self.balance["BTC"] += qty - (fee_cost if self.fee_ccy == "BTC" else 0)
        else:
            self.balance["BTC"] -= qty
            self.balance["USDT"] += qty * px - (fee_cost if self.fee_ccy == "USDT" else 0)
        return {
            "id": "1",
            "status": "closed",
            "filled": qty,
            "average": px,
            "cost": qty * px,
            "timestamp": 1_700_000_000_000,
            "fee": {"cost": fee_cost, "currency": self.fee_ccy},
        }


def test_ccxt_broker_rounds_and_converts_fees():
    ex = FakeCcxt(fee_ccy="BTC")
    b = CcxtBroker(ex, "BTC/USDT", 0.001)
    fill = b.buy(1.23456, 100.0, NOW, "t")
    assert fill.qty == pytest.approx(1.234)  # redondeado a la precisión del mercado
    assert fill.fee == pytest.approx(1.234 * 0.001 * 100)  # comisión en BTC pasada a USDT
    assert b.base_qty == pytest.approx(1.234 * 0.999)  # saldo real tras la comisión
    assert b.buy(0.0001, 100.0, NOW, "t") is None  # por debajo del mínimo


def test_ccxt_broker_never_sells_more_than_held():
    ex = FakeCcxt(fee_ccy="USDT")
    b = CcxtBroker(ex, "BTC/USDT", 0.001)
    b.buy(2, 100.0, NOW, "t")
    b.sell(50, 100.0, NOW, "t")
    assert ex.orders[-1][1] == pytest.approx(2)


def test_network_error_on_order_is_not_retried():
    ex = FakeCcxt(fail_create=True)
    b = CcxtBroker(ex, "BTC/USDT", 0.001)
    with pytest.raises(OrderUncertainError):
        b.buy(1, 100.0, NOW, "t")
    assert ex.orders == []


def test_testnet_switches_urls():
    real = make_exchange(ExchangeConfig(id="binance", testnet=False))
    test = make_exchange(ExchangeConfig(id="binance", testnet=True))
    assert real.urls["api"] != test.urls["api"]


def test_exchange_without_testnet_fails_instead_of_going_live():
    with pytest.raises(ccxt.NotSupported):
        make_exchange(ExchangeConfig(id="kraken", testnet=True))


def test_live_real_money_is_blocked_by_default(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"exchange: {{testnet: false}}\nlog_dir: {tmp_path / 'logs'}\n")
    monkeypatch.delenv("TRADEBOT_LIVE_CONFIRM", raising=False)
    assert main(["-c", str(cfg), "live"]) == 2
    out = capsys.readouterr().out
    assert "BLOQUEADO" in out and "live.enabled" in out and "TRADEBOT_LIVE_CONFIRM" in out


def test_live_real_money_requires_typed_confirmation(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"exchange: {{testnet: false}}\nlive: {{enabled: true}}\nlog_dir: {tmp_path / 'logs'}\n"
    )
    monkeypatch.setenv("TRADEBOT_LIVE_CONFIRM", "YES")
    monkeypatch.setattr("builtins.input", lambda _: "ETH/USDT")
    assert main(["-c", str(cfg), "live"]) == 2
