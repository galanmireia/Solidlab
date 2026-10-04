"""Línea de comandos: ``tradebot backtest | paper | live | status``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from tradebot.backtest import run_backtest, split_in_out_of_sample
from tradebot.config import AppConfig, load_config
from tradebot.data import load_or_fetch, synthetic_ohlcv
from tradebot.journal import Journal
from tradebot.metrics import format_metrics
from tradebot.risk import RiskManager
from tradebot.strategies import build_strategy
from tradebot.trader import Trader

log = logging.getLogger("tradebot")


def setup_logging(cfg: AppConfig, name: str) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = RotatingFileHandler(cfg.log_dir / f"{name}.log", maxBytes=5_000_000, backupCount=5)
    file.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file)
    logging.getLogger("ccxt").setLevel(logging.WARNING)


def _state_path(cfg: AppConfig, mode: str) -> Path:
    safe = cfg.market.symbol.replace("/", "-")
    return cfg.state_dir / f"{mode}_{cfg.exchange.id}_{safe}_{cfg.market.timeframe}.json"


# ------------------------------------------------------------------ backtest
def cmd_backtest(cfg: AppConfig, args: argparse.Namespace) -> int:
    if args.synthetic:
        log.warning("Usando datos SINTÉTICOS: sirve para probar el software, no la estrategia")
        df = synthetic_ohlcv(timeframe=cfg.market.timeframe, seed=args.seed)
    else:
        from tradebot.config import ExchangeConfig
        from tradebot.exchange import make_exchange

        # Los datos históricos se leen siempre del exchange real (público, sin claves).
        exchange = make_exchange(ExchangeConfig(id=cfg.exchange.id, testnet=False))
        df = load_or_fetch(
            exchange,
            cfg.market.symbol,
            cfg.market.timeframe,
            cfg.backtest.start,
            cfg.backtest.end,
            cfg.data_dir,
            refresh=args.refresh,
        )
    log.info("Datos: %d velas de %s a %s", len(df), df.index[0], df.index[-1])

    journal = Journal(cfg.journal_dir, "backtest") if args.journal else None
    segments = [("COMPLETO", df)]
    if args.oos:
        ins, oos = split_in_out_of_sample(df, 1 - args.oos)
        segments = [("IN-SAMPLE (ajuste)", ins), ("OUT-OF-SAMPLE (validación)", oos)]

    for label, segment in segments:
        result = run_backtest(segment, cfg, journal=journal)
        print(f"\n=== {cfg.strategy.name} {cfg.market.symbol} {cfg.market.timeframe} — {label} ===")
        print(f"  Periodo: {segment.index[0]:%Y-%m-%d} → {segment.index[-1]:%Y-%m-%d}")
        print(f"  Parámetros: {result.params}")
        print(format_metrics(result.metrics))
        if result.halted:
            print(f"  ⚠ Cortacircuitos activado: {result.halt_reason}")
        if args.equity_csv:
            out = Path(args.equity_csv)
            out = out.with_name(f"{out.stem}_{label.split()[0].lower()}{out.suffix}")
            result.equity.to_csv(out)
            print(f"  Curva de capital guardada en {out}")
    return 0


# -------------------------------------------------------------- paper / live
def _build_trader(cfg: AppConfig, broker, mode: str) -> Trader:
    strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
    journal = Journal(cfg.journal_dir, mode)
    return Trader(cfg.market.symbol, strategy, RiskManager(cfg.risk), broker, journal)


def _run(runner, initial_cash: float | None, max_steps: int | None) -> None:
    """Arranca el bucle, con Telegram si hay TELEGRAM_BOT_TOKEN."""
    from tradebot.commands import make_handler
    from tradebot.telegram import TelegramBot

    bot = TelegramBot.from_env()
    if bot is None:
        log.info("Telegram desactivado (no hay TELEGRAM_BOT_TOKEN)")
    else:
        runner.notify = bot.send
        runner.trader.listeners.append(bot.send)
        bot.start_polling(make_handler(runner, initial_cash))
        if bot.chat_id is None:
            log.warning("Falta TELEGRAM_CHAT_ID: escribe al bot y te dirá cuál es el tuyo")
        else:
            log.info("Telegram activado")
    try:
        runner.run(max_steps=max_steps)
    finally:
        if bot:
            bot.stop()


def cmd_paper(cfg: AppConfig, args: argparse.Namespace) -> int:
    from tradebot.brokers.simulated import SimulatedBroker
    from tradebot.config import ExchangeConfig
    from tradebot.exchange import make_exchange
    from tradebot.runner import LiveRunner

    # Precios reales del mercado (públicos), dinero simulado.
    exchange = make_exchange(ExchangeConfig(id=cfg.exchange.id, testnet=False))
    broker = SimulatedBroker(
        cfg.market.symbol, cfg.paper.initial_cash, cfg.costs.fee_rate, cfg.costs.slippage_bps
    )
    trader = _build_trader(cfg, broker, "paper")
    runner = LiveRunner(
        trader, exchange, cfg.market.timeframe, _state_path(cfg, "paper"), cfg.paper.poll_seconds
    )
    log.info("PAPER TRADING: precios reales, dinero simulado (%.2f)", cfg.paper.initial_cash)
    _run(runner, cfg.paper.initial_cash, args.max_steps)
    return 0


def cmd_live(cfg: AppConfig, args: argparse.Namespace) -> int:
    from tradebot.brokers.ccxt_broker import CcxtBroker
    from tradebot.exchange import make_exchange
    from tradebot.runner import LiveRunner

    real_money = not cfg.exchange.testnet
    if real_money:
        problems = []
        if not cfg.live.enabled:
            problems.append("live.enabled es false en la configuración")
        if os.getenv("TRADEBOT_LIVE_CONFIRM") != "YES":
            problems.append("falta la variable de entorno TRADEBOT_LIVE_CONFIRM=YES")
        if problems:
            print("Modo real BLOQUEADO:\n  - " + "\n  - ".join(problems))
            return 2
        print(f"\n⚠  VAS A OPERAR CON DINERO REAL en {cfg.exchange.id}: {cfg.market.symbol}")
        typed = input(f"Escribe '{cfg.market.symbol}' para confirmar: ").strip()
        if typed != cfg.market.symbol:
            print("Confirmación incorrecta. Cancelado.")
            return 2

    exchange = make_exchange(cfg.exchange, authenticated=True)
    broker = CcxtBroker(exchange, cfg.market.symbol, cfg.costs.fee_rate)
    mode = "live" if real_money else "testnet"
    trader = _build_trader(cfg, broker, mode)
    runner = LiveRunner(
        trader, exchange, cfg.market.timeframe, _state_path(cfg, mode), cfg.live.poll_seconds
    )
    log.info("MODO %s: saldo %s=%.2f", mode.upper(), broker.quote_ccy, broker.cash)
    _run(runner, None, args.max_steps)
    return 0


def cmd_status(cfg: AppConfig, args: argparse.Namespace) -> int:
    found = False
    for mode in ("paper", "testnet", "live"):
        path = _state_path(cfg, mode)
        if path.exists():
            found = True
            print(f"--- {mode} ({path}) ---")
            print(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2))
    if not found:
        print("No hay estado guardado para esta configuración.")
    return 0


# ---------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="tradebot", description=__doc__)
    parser.add_argument(
        "-c",
        "--config",
        default=os.getenv("TRADEBOT_CONFIG"),
        help="ruta al YAML de configuración (o variable TRADEBOT_CONFIG)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="probar la estrategia con datos históricos")
    bt.add_argument("--synthetic", action="store_true", help="datos simulados (sin internet)")
    bt.add_argument("--seed", type=int, default=42)
    bt.add_argument(
        "--oos",
        type=float,
        default=0.3,
        help="fracción final reservada para validación (0 = sin dividir)",
    )
    bt.add_argument("--refresh", action="store_true", help="volver a descargar los datos")
    bt.add_argument("--journal", action="store_true", help="guardar operaciones en CSV")
    bt.add_argument("--equity-csv", default=None, help="guardar la curva de capital")

    for name, helptext in (
        ("paper", "simulación en tiempo real con precios reales"),
        ("live", "testnet o dinero real (protegido)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--max-steps", type=int, default=None, help=argparse.SUPPRESS)

    sub.add_parser("status", help="ver el estado guardado del bot")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg, args.command)
    handlers = {
        "backtest": cmd_backtest,
        "paper": cmd_paper,
        "live": cmd_live,
        "status": cmd_status,
    }
    return handlers[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
