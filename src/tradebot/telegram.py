"""Control y avisos por Telegram.

Variables de entorno:
- ``TELEGRAM_BOT_TOKEN``: token que da @BotFather.
- ``TELEGRAM_CHAT_ID``: tu chat. Si no está puesto, el bot solo responde diciéndote
  cuál es tu chat id (para que lo configures) y no acepta ningún otro comando.

Solo se aceptan comandos del chat configurado: nadie más puede controlar el bot.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token: str, chat_id: str | None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.session = requests.Session()
        self._offset: int | None = None
        self._stop = threading.Event()

    @staticmethod
    def from_env() -> TelegramBot | None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return None
        return TelegramBot(token, os.getenv("TELEGRAM_CHAT_ID", "").strip() or None)

    def _call(self, method: str, timeout: float = 15, **params) -> dict:
        resp = self.session.post(
            API.format(token=self.token, method=method), json=params, timeout=timeout
        )
        resp.raise_for_status()
        return resp.json()

    def send(self, text: str, chat_id: str | None = None) -> None:
        """Nunca lanza excepciones: un fallo de Telegram no debe parar el trading."""
        target = chat_id or self.chat_id
        if not target:
            return
        try:
            self._call("sendMessage", chat_id=target, text=text[:4000])
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo enviar a Telegram: %s", exc)

    def start_polling(self, handle: Callable[[str], str]) -> threading.Thread:
        """Escucha comandos en otro hilo. ``handle`` recibe el texto y devuelve la respuesta."""
        thread = threading.Thread(target=self._poll, args=(handle,), daemon=True, name="telegram")
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def _poll(self, handle: Callable[[str], str]) -> None:
        while not self._stop.is_set():
            try:
                params = {"timeout": 25, "allowed_updates": ["message"]}
                if self._offset is not None:
                    params["offset"] = self._offset
                updates = self._call("getUpdates", timeout=35, **params).get("result", [])
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram getUpdates falló: %s", exc)
                self._stop.wait(10)
                continue
            for upd in updates:
                self._offset = upd["update_id"] + 1
                self._dispatch(upd.get("message") or {}, handle)

    def _dispatch(self, msg: dict, handle: Callable[[str], str]) -> None:
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            return
        if self.chat_id is None:
            self.send(
                f"Tu chat id es {chat}. Configúralo como TELEGRAM_CHAT_ID en Railway "
                "para poder controlar el bot.",
                chat_id=chat,
            )
            return
        if chat != self.chat_id:
            log.warning("Comando ignorado de un chat no autorizado (%s)", chat)
            return
        try:
            reply = handle(text)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error procesando comando de Telegram")
            reply = f"Error: {exc}"
        self.send(reply)
