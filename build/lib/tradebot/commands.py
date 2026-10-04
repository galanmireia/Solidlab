"""Comandos de chat (Telegram) para consultar y controlar el bot en marcha."""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from tradebot.runner import LiveRunner

HELP = (
    "Comandos:\n"
    "/estado – precio, capital, posición y stop\n"
    "/operaciones – últimas 5 operaciones cerradas\n"
    "/resumen – resultado total desde el inicio\n"
    "/pausa – no abrir operaciones nuevas (la posición abierta sigue con su stop)\n"
    "/reanudar – volver a operar\n"
    "/ayuda – esta ayuda"
)


def _read_trades(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def make_handler(runner: LiveRunner, initial_cash: float | None = None) -> Callable[[str], str]:
    trader = runner.trader
    trades_path = trader.journal.trades_path if trader.journal else None

    def estado() -> str:
        price = runner.last_price
        if price is None:
            return "Arrancando, todavía no hay precio. Prueba en unos segundos."
        eq = trader.equity(price)
        rs = trader.risk.state
        lines = [
            f"📊 {trader.symbol} ({runner.mode})",
            f"Precio: {price:,.2f}",
            f"Capital: {eq:,.2f}",
        ]
        if initial_cash:
            lines.append(f"Desde el inicio: {eq / initial_cash - 1:+.2%}")
        pos = trader.position
        if pos:
            unreal = pos.qty * (price - pos.entry_price) - pos.entry_fee
            lines += [
                "",
                f"Posición: {pos.qty:.6f} desde {pos.entry_price:,.2f}",
                f"Stop: {pos.stop_price:,.2f} ({pos.stop_price / price - 1:+.2%})",
                f"Latente: {unreal:+,.2f}",
            ]
        else:
            lines += ["", "Sin posición: esperando señal."]
        if rs.halted:
            lines.append(f"\n⛔ DETENIDO: {rs.halt_reason}")
        elif rs.paused:
            lines.append("\n⏸ En pausa (no abre operaciones nuevas)")
        if runner.last_bar_ts is not None:
            lines.append(f"\nÚltima vela analizada: {runner.last_bar_ts:%d/%m %H:%M} UTC")
        return "\n".join(lines)

    def operaciones() -> str:
        rows = _read_trades(trades_path)[-5:]
        if not rows:
            return "Todavía no hay operaciones cerradas."
        out = ["Últimas operaciones:"]
        for r in reversed(rows):
            when = pd.Timestamp(r["exit_time"]).strftime("%d/%m %H:%M")
            pnl = float(r["pnl"])
            icon = "✅" if pnl > 0 else "🔴"
            out.append(f"{icon} {when}  {pnl:+,.2f} ({r['return_pct']})  {r['exit_reason']}")
        return "\n".join(out)

    def resumen() -> str:
        rows = _read_trades(trades_path)
        if not rows:
            return "Todavía no hay operaciones cerradas."
        pnls = [float(r["pnl"]) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        pf = sum(wins) / -sum(losses) if sum(losses) < 0 else float("inf")
        return (
            f"Operaciones: {len(pnls)}\n"
            f"Resultado neto: {sum(pnls):+,.2f}\n"
            f"Aciertos: {len(wins) / len(pnls):.0%}\n"
            f"Profit factor: {pf:.2f}\n"
            f"Mejor: {max(pnls):+,.2f} · Peor: {min(pnls):+,.2f}"
        )

    def pausa() -> str:
        trader.risk.state.paused = True
        runner.save_state()
        return "⏸ En pausa. No abrirá operaciones nuevas; si hay posición, mantiene su stop."

    def reanudar() -> str:
        trader.risk.state.paused = False
        runner.save_state()
        extra = ""
        if trader.risk.state.halted:
            extra = (
                f"\n⚠️ Pero el bot está DETENIDO por el cortacircuitos "
                f"({trader.risk.state.halt_reason}). Eso requiere revisión manual."
            )
        return "▶️ Reanudado." + extra

    commands: dict[str, Callable[[], str]] = {
        "/estado": estado,
        "/operaciones": operaciones,
        "/resumen": resumen,
        "/pausa": pausa,
        "/reanudar": reanudar,
        "/ayuda": lambda: HELP,
        "/start": lambda: HELP,
        "/help": lambda: HELP,
    }

    def handle(text: str) -> str:
        cmd = text.split()[0].split("@")[0].lower()
        fn = commands.get(cmd)
        if fn is None:
            return "No entiendo ese comando.\n\n" + HELP
        with runner.lock:
            return fn()

    return handle
