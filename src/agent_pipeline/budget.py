"""Restart-safe SQLite spend reservations for paid model calls."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Prices:
    prompt_per_million: Decimal
    completion_per_million: Decimal

    def __post_init__(self) -> None:
        if self.prompt_per_million <= 0 or self.completion_per_million <= 0:
            raise ValueError("Paid model prices must be positive")

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("Token usage cannot be negative")
        return (
            self.prompt_per_million * prompt_tokens
            + self.completion_per_million * completion_tokens
        ) / MILLION


class MoneyBudget:
    """SQLite reservations make the local cap survive crashes and concurrent processes."""

    def __init__(self, path: Path, limit_usd: Decimal) -> None:
        if limit_usd <= 0:
            raise ValueError("The local paid-test cap must be positive")
        self.path = path
        self.limit_usd = limit_usd
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, reserved TEXT NOT NULL, charged TEXT, state TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS allowance (id INTEGER PRIMARY KEY CHECK (id = 1), floor TEXT NOT NULL, spent_marker TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    def totals(self) -> tuple[Decimal, Decimal]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT reserved, charged, state FROM calls").fetchall()
        spent = sum(
            (Decimal(charged) for _, charged, state in rows if state == "settled"),
            Decimal(0),
        )
        reserved = sum(
            (Decimal(amount) for amount, _, state in rows if state == "reserved"),
            Decimal(0),
        )
        return spent, reserved

    def reserve(self, amount: Decimal, key_allowance: Decimal | None = None) -> str:
        if amount <= 0:
            raise ValueError("Reservation must be positive")
        call_id = uuid.uuid4().hex
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT reserved, charged, state FROM calls").fetchall()
            spent = sum(
                (Decimal(charged) for _, charged, state in rows if state == "settled"),
                Decimal(0),
            )
            outstanding = sum(
                (
                    Decimal(reserved)
                    for reserved, _, state in rows
                    if state == "reserved"
                ),
                Decimal(0),
            )
            committed = spent + outstanding
            if committed + amount > self.limit_usd:
                db.rollback()
                raise RuntimeError("Local paid-test cap exhausted")
            if key_allowance is not None:
                previous = db.execute(
                    "SELECT floor, spent_marker FROM allowance WHERE id = 1"
                ).fetchone()
                floor = key_allowance
                if previous:
                    floor = min(
                        floor, Decimal(previous[0]) - (spent - Decimal(previous[1]))
                    )
                if outstanding + amount > floor:
                    db.rollback()
                    raise RuntimeError("Confirmed key allowance is insufficient")
                db.execute(
                    "INSERT OR REPLACE INTO allowance (id, floor, spent_marker) VALUES (1, ?, ?)",
                    (str(floor), str(spent)),
                )
            db.execute(
                "INSERT INTO calls (id, reserved, charged, state) VALUES (?, ?, NULL, 'reserved')",
                (call_id, str(amount)),
            )
            db.commit()
        return call_id

    def settle(self, reservation_id: str, actual: Decimal | None) -> bool:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT reserved, state FROM calls WHERE id = ?", (reservation_id,)
            ).fetchone()
            if row is None or row[1] != "reserved":
                db.rollback()
                raise ValueError("Unknown or settled reservation")
            charge = Decimal(row[0]) if actual is None else actual
            if charge < 0:
                db.rollback()
                raise ValueError("Charge cannot be negative")
            db.execute(
                "UPDATE calls SET charged = ?, state = 'settled' WHERE id = ?",
                (str(charge), reservation_id),
            )
            db.commit()
            return charge > Decimal(row[0]) or charge > self.limit_usd
