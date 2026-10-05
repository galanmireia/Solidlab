"""Línea de comandos: ``tradebot backtest | paper | live | status``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
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
def cmd_backtest(cfgs: list[AppConfig], args: argparse.Namespace) -> int:
    for cfg in cfgs:
        _backtest_one(cfg, args)
    return 0


def _backtest_one(cfg: AppConfig, args: argparse.Namespace) -> None:
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
        print(
            f"\n=== {cfg.name} · {cfg.strategy.name} · {_coins(cfg)} · "
            f"{cfg.market.timeframe} — {label} ==="
        )
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


# -------------------------------------------------------------- paper / live
def _build_portfolio(cfg: AppConfig, brokers: dict, mode: str) -> Portfolio:
    journal = Journal(cfg.journal_dir, f"{mode}_{cfg.exchange.id}")
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


def _run_all(entries: list[tuple[AppConfig, object, float | None]], max_steps: int | None) -> None:
    """Arranca uno o varios portfolios a la vez, con Telegram si hay TELEGRAM_BOT_TOKEN."""
    from tradebot.commands import Book, make_handler
    from tradebot.telegram import TelegramBot

    multi = len(entries) > 1
    bot = TelegramBot.from_env()
    if bot is None:
        log.info("Telegram desactivado (no hay TELEGRAM_BOT_TOKEN)")
    else:
        for cfg, runner, _ in entries:

            def send(text: str, _name: str = cfg.name) -> None:
                bot.send(f"[{_name}] {text}" if multi else text)

            runner.notify = send
            runner.portfolio.add_listener(send)
        bot.start_polling(make_handler([Book(c.name, r, cash) for c, r, cash in entries]))
        if bot.chat_id is None:
            log.warning("Falta TELEGRAM_CHAT_ID: escribe al bot y te dirá cuál es el tuyo")
        else:
            log.info("Telegram activado")

    def guarded(cfg: AppConfig, runner) -> None:
        failures = 0
        while True:
            try:
                runner.run(max_steps=max_steps)
                return
            except Exception as exc:  # noqa: BLE001 - que un portfolio caído no tumbe al resto
                failures += 1
                log.exception("El portfolio %s se ha detenido", cfg.name)
                if not multi or max_steps is not None:
                    raise
                if failures == 1:
                    runner.notify(
                        f"⛔ {cfg.name} no puede funcionar ahora mismo ({exc}). "
                        "Lo reintento cada 10 minutos; te aviso cuando vuelva."
                    )
                runner.sleep(600)

    try:
        if not multi:
            cfg, runner, _ = entries[0]
            guarded(cfg, runner)
        else:
            threads = [
                threading.Thread(target=guarded, args=(c, r), name=c.name, daemon=True)
                for c, r, _ in entries
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
    finally:
        if bot:
            bot.stop()


def _paper_runner(cfg: AppConfig):
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
    log.info(
        "PAPER TRADING %s (%s): precios reales, dinero simulado (%.2f)",
        cfg.name,
        _coins(cfg),
        cfg.paper.initial_cash,
    )
    return LiveRunner(
        portfolio,
        exchange,
        cfg.market.timeframe,
        _state_path(cfg, "paper"),
        cfg.paper.poll_seconds,
    )


def cmd_paper(cfgs: list[AppConfig], args: argparse.Namespace) -> int:
    entries = [(cfg, _paper_runner(cfg), cfg.paper.initial_cash) for cfg in cfgs]
    _run_all(entries, args.max_steps)
    return 0


def cmd_live(cfgs: list[AppConfig], args: argparse.Namespace) -> int:
    if len(cfgs) != 1:
        print("El modo live admite una sola configuración a la vez.")
        return 2
    cfg = cfgs[0]
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
    _run_all([(cfg, runner, None)], args.max_steps)
    return 0


def cmd_status(cfgs: list[AppConfig], args: argparse.Namespace) -> int:
    for cfg in cfgs:
        _status_one(cfg)
    return 0


def _status_one(cfg: AppConfig) -> None:
    found = False
    for mode in ("paper", "testnet", "live"):
        path = _state_path(cfg, mode)
        if path.exists():
            found = True
            print(f"--- {mode} ({path}) ---")
            print(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2))
    if not found:
        print(f"No hay estado guardado para {cfg.name}.")


# ---------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="tradebot", description=__doc__)
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        help="YAML de configuración; repetible para varios portfolios "
        "(o TRADEBOT_CONFIG con rutas separadas por comas)",
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
    paths = args.config or [p for p in os.getenv("TRADEBOT_CONFIG", "").split(",") if p.strip()]
    cfgs = [load_config(p.strip()) for p in paths] or [load_config(None)]
    names = [c.name for c in cfgs]
    if len(set(names)) != len(names):
        parser.error(f"Los portfolios deben tener nombres distintos (name:): {names}")
    setup_logging(cfgs[0], args.command)
    handlers = {
        "backtest": cmd_backtest,
        "paper": cmd_paper,
        "live": cmd_live,
        "status": cmd_status,
    }
    return handlers[args.command](cfgs, args)


if __name__ == "__main__":
    sys.exit(main())
