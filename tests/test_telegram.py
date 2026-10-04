from __future__ import annotations

import pandas as pd
from test_runner import FakeExchange, make_runner

from tradebot.commands import make_handler
from tradebot.journal import Journal
from tradebot.models import Action, Signal
from tradebot.telegram import TelegramBot


class RecordingBot(TelegramBot):
    def __init__(self, chat_id):
        super().__init__("token", chat_id)
        self.sent: list[tuple[str, str | None]] = []

    def send(self, text, chat_id=None):
        self.sent.append((text, chat_id or self.chat_id))


def msg(chat, text):
    return {"chat": {"id": chat}, "text": text}


def test_only_authorized_chat_can_control():
    bot = RecordingBot("111")
    calls = []
    bot._dispatch(msg(999, "/pausa"), lambda t: calls.append(t) or "ok")
    assert calls == [] and bot.sent == []  # intruso: ignorado sin respuesta
    bot._dispatch(msg(111, "/estado"), lambda t: calls.append(t) or "ok")
    assert calls == ["/estado"] and bot.sent == [("ok", "111")]


def test_without_chat_id_only_reveals_chat_id():
    bot = RecordingBot(None)
    calls = []
    bot._dispatch(msg(555, "/pausa"), lambda t: calls.append(t) or "ok")
    assert calls == []
    assert "555" in bot.sent[0][0] and bot.sent[0][1] == "555"


def _running(tmp_path, script):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    ex = FakeExchange(start)
    ex.now = start + pd.Timedelta(hours=9)
    runner = make_runner(tmp_path, script, ex)
    runner.trader.journal = Journal(tmp_path / "journal", "paper")
    runner.bootstrap()
    return runner, ex, start


def test_commands_and_notifications(tmp_path):
    runner, ex, start = _running(tmp_path, {0: Signal(Action.ENTER, 90, "in")})
    events: list[str] = []
    runner.trader.listeners.append(events.append)
    handle = make_handler(runner, initial_cash=10_000)

    runner.step()
    ex.now = start + pd.Timedelta(hours=12, minutes=1)
    runner.step()
    assert any("COMPRA" in e for e in events)
    status = handle("/estado")
    assert "Posición" in status and "Stop: 90.00" in status

    ex.price = 89
    runner.step()
    assert any("VENTA" in e and "stop-loss" in e for e in events)
    assert "stop-loss" in handle("/operaciones")
    assert "Operaciones: 1" in handle("/resumen")
    assert "No entiendo" in handle("/xyz")


def test_pause_blocks_new_entries(tmp_path):
    runner, ex, start = _running(tmp_path, {0: Signal(Action.ENTER, 90, "in")})
    handle = make_handler(runner)
    assert "pausa" in handle("/pausa").lower()
    ex.now = start + pd.Timedelta(hours=12, minutes=1)
    runner.step()
    assert runner.trader.position is None
    assert "Reanudado" in handle("/reanudar")
    assert runner.trader.risk.state.paused is False


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    """Imita requests.Session contra la API de Telegram."""

    def __init__(self, bot, updates):
        self.bot, self.updates, self.posts = bot, updates, []

    def post(self, url, json, timeout):
        self.posts.append((url.rsplit("/", 1)[-1], json, timeout))
        if url.endswith("getUpdates"):
            batch, self.updates = self.updates, []
            if not batch:
                self.bot.stop()
            return FakeResponse({"ok": True, "result": batch})
        return FakeResponse({"ok": True})


def test_polling_end_to_end_with_http_layer():
    bot = TelegramBot("token", "111")
    bot.session = FakeSession(bot, [{"update_id": 7, "message": msg(111, "/estado")}])
    bot._stop.wait = lambda _t: bot._stop.set()  # ante un error, terminar en vez de reintentar
    bot._poll(lambda t: f"respuesta a {t}")
    kinds = [(m, p) for m, p, _ in bot.session.posts]
    assert kinds[0][0] == "getUpdates" and kinds[0][1]["timeout"] == 25
    assert ("sendMessage", {"chat_id": "111", "text": "respuesta a /estado"}) in kinds
    assert bot.session.posts[-1][1]["offset"] == 8  # no reprocesa mensajes ya leídos
    assert bot.session.posts[0][2] > 25  # timeout HTTP mayor que el long-polling
