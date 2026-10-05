"""Comandos de chat (Telegram) para consultar y controlar el bot en marcha."""

from __future__ import annotations

import csv
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tradebot.runner import LiveRunner

HELP = (
    "Comandos:\n"
    "/estado – capital, posiciones abiertas y stops\n"
    "/precios – precio actual de cada activo vigilado\n"
    "/operaciones – últimas 5 operaciones cerradas\n"
    "/resumen – resultado total y por activo\n"
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


@dataclass
class Book:
    """Un portfolio en marcha (p. ej. "Cripto" o "Bolsa") con su capital inicial."""

    name: str
    runner: LiveRunner
    initial_cash: float | None = None

    @property
    def portfolio(self):
        return self.runner.portfolio

    @property
    def trades_path(self) -> Path | None:
        first = next(iter(self.portfolio.traders.values()))
        return first.journal.trades_path if first.journal else None


def make_handler(
    books: LiveRunner | list[Book], initial_cash: float | None = None
) -> Callable[[str], str]:
    if isinstance(books, LiveRunner):
        books = [Book("Portfolio", books, initial_cash)]
    multi = len(books) > 1

    def title(book: Book, icon: str) -> str:
        return f"{icon} {book.name} ({book.runner.mode})"

    def estado_one(book: Book) -> str:
        pf, runner = book.portfolio, book.runner
        if not pf.prices:
            return f"{title(book, '📊')}\nArrancando, todavía no hay precios."
        eq = pf.equity()
        lines = [title(book, "📊"), f"Capital: {eq:,.2f}"]
        if book.initial_cash:
            lines.append(f"Desde el inicio: {eq / book.initial_cash - 1:+.2%}")
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

    def estado() -> str:
        return "\n\n— — —\n\n".join(estado_one(b) for b in books)

    def precios() -> str:
        out = []
        for b in books:
            pf = b.portfolio
            lines = [title(b, "💱")]
            for sym in pf.symbols:
                mark = "🟢" if pf.traders[sym].position else "·"
                price = pf.prices.get(sym)
                lines.append(
                    f"{mark} {_coin(sym)}: {price:,.4g}" if price else f"· {_coin(sym)}: —"
                )
            out.append("\n".join(lines))
        return "\n\n".join(out)

    def _all_trades() -> list[tuple[Book, dict]]:
        return [(b, r) for b in books for r in _read_trades(b.trades_path)]

    def operaciones() -> str:
        rows = sorted(_all_trades(), key=lambda br: br[1]["exit_time"])[-5:]
        if not rows:
            return "Todavía no hay operaciones cerradas."
        out = ["Últimas operaciones:"]
        for book, r in reversed(rows):
            when = pd.Timestamp(r["exit_time"]).strftime("%d/%m %H:%M")
            pnl = float(r["pnl"])
            icon = "✅" if pnl > 0 else "🔴"
            where = f"[{book.name}] " if multi else ""
            out.append(
                f"{icon} {where}{_coin(r['symbol'])} {when}  {pnl:+,.2f} ({r['return_pct']})  "
                f"{r['exit_reason']}"
            )
        return "\n".join(out)

    def resumen_one(book: Book) -> str:
        rows = _read_trades(book.trades_path)
        head = title(book, "📈")
        if not rows:
            return f"{head}\nTodavía no hay operaciones cerradas."
        pnls = [float(r["pnl"]) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        pf_ratio = sum(wins) / -sum(losses) if sum(losses) < 0 else float("inf")
        out = [
            head,
            f"Operaciones: {len(pnls)}",
            f"Resultado neto: {sum(pnls):+,.2f}",
            f"Aciertos: {len(wins) / len(pnls):.0%}",
            f"Profit factor: {pf_ratio:.2f}",
            "Por activo:",
        ]
        by_sym: dict[str, list[float]] = {}
        for r in rows:
            by_sym.setdefault(r["symbol"], []).append(float(r["pnl"]))
        for sym, vals in sorted(by_sym.items(), key=lambda kv: -sum(kv[1])):
            out.append(f"• {_coin(sym)}: {sum(vals):+,.2f} ({len(vals)} op.)")
        return "\n".join(out)

    def resumen() -> str:
        return "\n\n".join(resumen_one(b) for b in books)

    def pausa() -> str:
        for b in books:
            b.portfolio.risk.state.paused = True
            b.runner.save_state()
        return "⏸ En pausa. No abrirá operaciones nuevas; las abiertas mantienen su stop."

    def reanudar() -> str:
        extra = ""
        for b in books:
            rs = b.portfolio.risk.state
            rs.paused = False
            b.runner.save_state()
            if rs.halted:
                extra += (
                    f"\n⚠️ {b.name} está DETENIDO por el cortacircuitos ({rs.halt_reason}). "
                    "Eso requiere revisión manual."
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
        with ExitStack() as stack:
            for b in books:
                stack.enter_context(b.runner.lock)
            return fn()

    return handle
