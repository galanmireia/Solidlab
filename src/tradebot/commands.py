"""Comandos de chat (Telegram) para consultar y controlar el bot en marcha."""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from tradebot.runner import LiveRunner

HELP = (
    "Comandos:\n"
    "/estado – capital, posiciones abiertas y stops\n"
    "/precios – precio actual de cada moneda vigilada\n"
    "/operaciones – últimas 5 operaciones cerradas\n"
    "/resumen – resultado total y por moneda\n"
    "/pausa – no abrir operaciones nuevas (las abiertas siguen con su stop)\n"
    "/reanudar – volver a operar\n"
    "/ayuda – esta ayuda"
)


def _read_trades(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _coin(symbol: str) -> str:
    return symbol.split("/")[0]


def make_handler(runner: LiveRunner, initial_cash: float | None = None) -> Callable[[str], str]:
    pf = runner.portfolio
    first = next(iter(pf.traders.values()))
    trades_path = first.journal.trades_path if first.journal else None

    def estado() -> str:
        if not pf.prices:
            return "Arrancando, todavía no hay precios. Prueba en unos segundos."
        eq = pf.equity()
        lines = [f"📊 Portfolio ({runner.mode})", f"Capital: {eq:,.2f}"]
        if initial_cash:
            lines.append(f"Desde el inicio: {eq / initial_cash - 1:+.2%}")
        lines.append(f"Efectivo: {pf.cash:,.2f}")
        open_pos = [(s, t.position) for s, t in pf.traders.items() if t.position]
        lines.append(f"\nPosiciones: {len(open_pos)}/{pf.max_open_positions}")
        for sym, pos in open_pos:
            price = pf.prices.get(sym, pos.entry_price)
            unreal = pos.qty * (price - pos.entry_price) - pos.entry_fee
            lines += [
                f"\n• {_coin(sym)}: {pos.qty:.6g} desde {pos.entry_price:,.4g}",
                f"  Ahora {price:,.4g} · latente {unreal:+,.2f}",
                f"  Stop {pos.stop_price:,.4g} ({pos.stop_price / price - 1:+.1%})",
            ]
        if not open_pos:
            lines.append("Sin posiciones: esperando señales.")
        rs = pf.risk.state
        if rs.halted:
            lines.append(f"\n⛔ DETENIDO: {rs.halt_reason}")
        elif rs.paused:
            lines.append("\n⏸ En pausa (no abre operaciones nuevas)")
        if runner.last_bar_ts:
            last = max(runner.last_bar_ts.values())
            lines.append(f"\nÚltima vela analizada: {last:%d/%m %H:%M} UTC")
        return "\n".join(lines)

    def precios() -> str:
        if not pf.prices:
            return "Todavía no hay precios."
        lines = ["💱 Precios:"]
        for sym in pf.symbols:
            mark = "🟢" if pf.traders[sym].position else "·"
            price = pf.prices.get(sym)
            lines.append(f"{mark} {_coin(sym)}: {price:,.4g}" if price else f"· {_coin(sym)}: —")
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
            out.append(
                f"{icon} {_coin(r['symbol'])} {when}  {pnl:+,.2f} ({r['return_pct']})  "
                f"{r['exit_reason']}"
            )
        return "\n".join(out)

    def resumen() -> str:
        rows = _read_trades(trades_path)
        if not rows:
            return "Todavía no hay operaciones cerradas."
        pnls = [float(r["pnl"]) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        pf_ratio = sum(wins) / -sum(losses) if sum(losses) < 0 else float("inf")
        out = [
            f"Operaciones: {len(pnls)}",
            f"Resultado neto: {sum(pnls):+,.2f}",
            f"Aciertos: {len(wins) / len(pnls):.0%}",
            f"Profit factor: {pf_ratio:.2f}",
            "",
            "Por moneda:",
        ]
        by_sym: dict[str, list[float]] = {}
        for r in rows:
            by_sym.setdefault(r["symbol"], []).append(float(r["pnl"]))
        for sym, vals in sorted(by_sym.items(), key=lambda kv: -sum(kv[1])):
            out.append(f"• {_coin(sym)}: {sum(vals):+,.2f} ({len(vals)} op.)")
        return "\n".join(out)

    def pausa() -> str:
        pf.risk.state.paused = True
        runner.save_state()
        return "⏸ En pausa. No abrirá operaciones nuevas; las abiertas mantienen su stop."

    def reanudar() -> str:
        pf.risk.state.paused = False
        runner.save_state()
        extra = ""
        if pf.risk.state.halted:
            extra = (
                f"\n⚠️ Pero el bot está DETENIDO por el cortacircuitos "
                f"({pf.risk.state.halt_reason}). Eso requiere revisión manual."
            )
        return "▶️ Reanudado." + extra

    commands: dict[str, Callable[[], str]] = {
        "/estado": estado,
        "/precios": precios,
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
