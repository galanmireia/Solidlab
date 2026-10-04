"""Diario de operaciones en CSV: cada ejecución y cada operación cerrada."""

from __future__ import annotations

import csv
from pathlib import Path

from tradebot.models import Fill, Trade

FILL_FIELDS = ["timestamp", "symbol", "side", "qty", "price", "fee", "reason"]
TRADE_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "qty",
    "entry_price",
    "exit_price",
    "fees",
    "pnl",
    "return_pct",
    "exit_reason",
]


class Journal:
    def __init__(self, directory: Path, prefix: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.fills_path = directory / f"{prefix}_fills.csv"
        self.trades_path = directory / f"{prefix}_trades.csv"

    @staticmethod
    def _append(path: Path, fields: list[str], row: dict) -> None:
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if new:
                writer.writeheader()
            writer.writerow(row)

    def log_fill(self, f: Fill) -> None:
        self._append(
            self.fills_path,
            FILL_FIELDS,
            {
                "timestamp": f.timestamp.isoformat(),
                "symbol": f.symbol,
                "side": f.side.value,
                "qty": f"{f.qty:.8f}",
                "price": f"{f.price:.8f}",
                "fee": f"{f.fee:.8f}",
                "reason": f.reason,
            },
        )

    def log_trade(self, t: Trade) -> None:
        self._append(
            self.trades_path,
            TRADE_FIELDS,
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "qty": f"{t.qty:.8f}",
                "entry_price": f"{t.entry_price:.8f}",
                "exit_price": f"{t.exit_price:.8f}",
                "fees": f"{t.fees:.8f}",
                "pnl": f"{t.pnl:.2f}",
                "return_pct": f"{t.return_pct:.4%}",
                "exit_reason": t.exit_reason,
            },
        )
