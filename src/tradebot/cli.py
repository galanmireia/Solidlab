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

from tradebot.backtest import run_portfolio_backtest, split_in_out_of_sample
from tradebot.config import AppConfig, load_config
from tradebot.data import load_or_fetch, synthetic_ohlcv
from tradebot.journal import Journal
from tradebot.metrics import format_metrics
from tradebot.portfolio import Portfolio
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
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = RotatingFileHandler(cfg.log_dir / f"{name}.log", maxBytes=5_000_000, backupCount=5)
    file.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file)
    logging.getLogger("ccxt").setLevel(logging.WARNING)


def _state_path(cfg: AppConfig, mode: str) -> Path:
    return cfg.state_dir / f"{mode}_{cfg.exchange.id}_{cfg.market.timeframe}_portfolio.json"


def _coins(cfg: AppConfig) -> str:
    return ", ".join(s.split("/")[0] for s in cfg.market.symbols)


# ------------------------------------------------------------------ backtest
def cmd_backtest(cfg: AppConfig, args: argparse.Namespace) -> int:
    symbols = cfg.market.symbols
    if args.synthetic:
        log.warning("Usando datos SINTÉTICOS: sirve para probar el software, no la estrategia")
        data = {
            sym: synthetic_ohlcv(
                timeframe=cfg.market.timeframe, seed=args.seed + i, start_price=100.0 * (i + 1)
            )
            for i, sym in enumerate(symbols)
        }
    else:
        from tradebot.config import ExchangeConfig
        from tradebot.exchange import make_exchange

        # Los datos históricos se leen siempre del exchange real (público, sin claves).
        exchange = make_exchange(ExchangeConfig(id=cfg.exchange.id, testnet=False))
        data = {}
        for sym in symbols:
            data[sym] = load_or_fetch(
                exchange,
                sym,
                cfg.market.timeframe,
                cfg.backtest.start,
                cfg.backtest.end,
                cfg.data_dir,
                refresh=args.refresh,
            )
            log.info("%s: %d velas desde %s", sym, len(data[sym]), data[sym].index[0].date())

    journal = Journal(cfg.journal_dir, "backtest") if args.journal else None
    segments = [("COMPLETO", data)]
    if args.oos:
        ins, oos = split_in_out_of_sample(data, 1 - args.oos)
        segments = [("IN-SAMPLE (ajuste)", ins), ("OUT-OF-SAMPLE (validación)", oos)]

    for label, segment in segments:
        result = run_portfolio_backtest(segment, cfg, journal=journal)
        idx = result.equity.index
        print(f"\n=== {cfg.strategy.name} · {_coins(cfg)} · {cfg.market.timeframe} — {label} ===")
        print(f"  Periodo: {idx[0]:%Y-%m-%d} → {idx[-1]:%Y-%m-%d}")
        print(f"  Parámetros: {result.params}")
        print(f"  Máx. posiciones simultáneas: {cfg.risk.max_open_positions}")
        print(format_metrics(result.metrics))
        if len(symbols) > 1:
            print("  (Buy and hold = comprar todas a partes iguales y no tocar nada)")
            print("  Por moneda:")
            for sym, m in result.per_symbol.items():
                print(
                    f"    {sym:<12} {m['operaciones']:>4} op.  {m['resultado']:>+12,.2f}  "
                    f"aciertos {m['aciertos']:.0%}"
                )
        if result.halted:
            print(f"  ⚠ Cortacircuitos activado: {result.halt_reason}")
        if args.equity_csv:
            out = Path(args.equity_csv)
            out = out.with_name(f"{out.stem}_{label.split()[0].lower()}{out.suffix}")
            result.equity.to_csv(out)
            print(f"  Curva de capital guardada en {out}")
    return 0


# -------------------------------------------------------------- paper / live
def _build_portfolio(cfg: AppConfig, brokers: dict, mode: str) -> Portfolio:
    journal = Journal(cfg.journal_dir, mode)
    traders = [
        Trader(
            sym,
            build_strategy(cfg.strategy.name, cfg.strategy.params),
            RiskManager(cfg.risk),
            brokers[sym],
            journal,
        )
        for sym in cfg.market.symbols
    ]
    return Portfolio(traders, RiskManager(cfg.risk), cfg.risk.max_open_positions)


def _run(runner, initial_cash: float | None, max_steps: int | None) -> None:
    """Arranca el bucle, con Telegram si hay TELEGRAM_BOT_TOKEN."""
    from tradebot.commands import make_handler
    from tradebot.telegram import TelegramBot

    bot = TelegramBot.from_env()
    if bot is None:
        log.info("Telegram desactivado (no hay TELEGRAM_BOT_TOKEN)")
    else:
        runner.notify = bot.send
        runner.portfolio.add_listener(bot.send)
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
    from tradebot.brokers.simulated import PaperAccount, SimulatedBroker
    from tradebot.config import ExchangeConfig
    from tradebot.exchange import make_exchange
    from tradebot.runner import LiveRunner

    # Precios reales del mercado (públicos), dinero simulado.
    exchange = make_exchange(ExchangeConfig(id=cfg.exchange.id, testnet=False))
    account = PaperAccount(cfg.paper.initial_cash)
    brokers = {
        sym: SimulatedBroker(sym, None, cfg.costs.fee_rate, cfg.costs.slippage_bps, account=account)
        for sym in cfg.market.symbols
    }
    portfolio = _build_portfolio(cfg, brokers, "paper")
    runner = LiveRunner(
        portfolio,
        exchange,
        cfg.market.timeframe,
        _state_path(cfg, "paper"),
        cfg.paper.poll_seconds,
    )
    log.info(
        "PAPER TRADING (%s): precios reales, dinero simulado (%.2f)",
        _coins(cfg),
        cfg.paper.initial_cash,
    )
    _run(runner, cfg.paper.initial_cash, args.max_steps)
    return 0


def cmd_live(cfg: AppConfig, args: argparse.Namespace) -> int:
    from tradebot.brokers.ccxt_broker import CcxtBroker, SharedBalance
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
        expected = "OPERAR CON DINERO REAL"
        print(f"\n⚠  VAS A OPERAR CON DINERO REAL en {cfg.exchange.id}: {_coins(cfg)}")
        typed = input(f"Escribe '{expected}' para confirmar: ").strip()
        if typed != expected:
            print("Confirmación incorrecta. Cancelado.")
            return 2

    exchange = make_exchange(cfg.exchange, authenticated=True)
    exchange.load_markets()
    balance = SharedBalance(exchange)
    brokers = {
        sym: CcxtBroker(exchange, sym, cfg.costs.fee_rate, balance=balance)
        for sym in cfg.market.symbols
    }
    mode = "live" if real_money else "testnet"
    portfolio = _build_portfolio(cfg, brokers, mode)
    runner = LiveRunner(
        portfolio, exchange, cfg.market.timeframe, _state_path(cfg, mode), cfg.live.poll_seconds
    )
    log.info("MODO %s: efectivo=%.2f", mode.upper(), portfolio.cash)
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
