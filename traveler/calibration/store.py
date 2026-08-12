"""Fare observation store.

SQLite via the standard library, so the store itself carries no dependency
weight. Historical series must be *accumulated* -- no public API hands you a
back catalogue of what a route cost last March -- so the value of this file
compounds only if snapshots run on a schedule.

A failed or partial poll must never corrupt the store: writes go through a
single transaction per batch and duplicate observations are idempotent.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path.home() / ".traveler" / "fares.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    origin       TEXT    NOT NULL,
    destination  TEXT    NOT NULL,
    depart_date  TEXT    NOT NULL,
    return_date  TEXT    NOT NULL DEFAULT '',
    cabin        TEXT    NOT NULL,
    observed_at  TEXT    NOT NULL,
    days_out     INTEGER NOT NULL,
    price        REAL    NOT NULL,
    currency     TEXT    NOT NULL,
    carrier      TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL,
    UNIQUE (origin, destination, depart_date, return_date, cabin,
            observed_at, price, carrier, source)
);
CREATE INDEX IF NOT EXISTS idx_route
    ON observations (origin, destination, depart_date);
CREATE INDEX IF NOT EXISTS idx_days_out ON observations (days_out);
"""


@dataclass(frozen=True)
class FareObservation:
    """One price seen for one itinerary at one moment."""

    origin: str
    destination: str
    depart_date: _dt.date
    cabin: str
    price: float
    currency: str
    observed_at: _dt.datetime
    return_date: _dt.date | None = None
    carrier: str | None = None
    source: str = "unknown"

    @property
    def days_out(self) -> int:
        return (self.depart_date - self.observed_at.date()).days

    def as_row(self) -> tuple:
        # Empty strings rather than NULL for the optional columns: SQLite
        # treats NULLs as distinct inside a UNIQUE constraint, so a one-way
        # observation would never dedupe against itself and every re-run of
        # a snapshot would append duplicates.
        return (
            self.origin.upper(),
            self.destination.upper(),
            self.depart_date.isoformat(),
            self.return_date.isoformat() if self.return_date else "",
            self.cabin,
            self.observed_at.isoformat(timespec="seconds"),
            self.days_out,
            float(self.price),
            self.currency,
            self.carrier or "",
            self.source,
        )


class FareStore:
    """Append-only store of observed fares."""

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record(self, observations: Iterable[FareObservation]) -> int:
        """Persist a batch atomically. Returns rows actually inserted.

        Duplicates are ignored rather than raising, so a re-run of the same
        snapshot is harmless.
        """
        rows = [obs.as_row() for obs in observations]
        if not rows:
            return 0
        with self._connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT OR IGNORE INTO observations
                    (origin, destination, depart_date, return_date, cabin,
                     observed_at, days_out, price, currency, carrier, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return conn.total_changes - before

    def series(self, origin: str, destination: str,
               cabin: str | None = None) -> list[sqlite3.Row]:
        """Every observation for a route, oldest first."""
        sql = (
            "SELECT * FROM observations WHERE origin = ? AND destination = ?"
        )
        params: list[object] = [origin.upper(), destination.upper()]
        if cabin:
            sql += " AND cabin = ?"
            params.append(cabin)
        sql += " ORDER BY depart_date, observed_at"
        with self._connect() as conn:
            return list(conn.execute(sql, params))

    def departures(self, origin: str, destination: str) -> list[str]:
        """Distinct departure dates tracked for a route."""
        with self._connect() as conn:
            return [
                r["depart_date"] for r in conn.execute(
                    "SELECT DISTINCT depart_date FROM observations "
                    "WHERE origin = ? AND destination = ? ORDER BY depart_date",
                    (origin.upper(), destination.upper()),
                )
            ]

    def routes(self) -> list[tuple[str, str]]:
        with self._connect() as conn:
            return [
                (r["origin"], r["destination"]) for r in conn.execute(
                    "SELECT DISTINCT origin, destination FROM observations"
                )
            ]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    def normalised_curve(self, origin: str, destination: str,
                         cabin: str | None = None) -> list[tuple[int, float]]:
        """(days_out, price / that departure's minimum) pairs.

        Normalising each departure by its own realised minimum is what makes
        observations from different routes and seasons comparable, and is the
        input the curve fit needs.
        """
        rows = self.series(origin, destination, cabin)
        by_departure: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_departure.setdefault(row["depart_date"], []).append(row)

        out: list[tuple[int, float]] = []
        for observations in by_departure.values():
            floor = min(r["price"] for r in observations)
            if floor <= 0:
                continue
            for r in observations:
                out.append((r["days_out"], r["price"] / floor))
        return sorted(out)
