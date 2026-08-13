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
import statistics
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path.home() / ".traveler" / "fares.db"

#: A departure needs at least this many observations before its own minimum
#: is treated as the realised trough rather than "cheapest seen so far".
MIN_OBSERVATIONS_FOR_REALISED_MIN = 4

#: Sources whose departure dates are synthesised from a lead-time column and
#: therefore carry no real calendar meaning. Seasonality is not applied to
#: them -- doing so would inject noise from a date that never existed.
SYNTHETIC_DATE_SOURCES = frozenset({"kaggle-easemytrip-2022"})


@dataclass(frozen=True)
class CurvePoint:
    """One normalised observation, carrying what the fit needs to weight it."""

    days_out: int
    ratio: float
    observed_at: str
    source: str
    #: Which reference the ratio was computed against.
    basis: str = "route_reference"

    @property
    def observed_date(self) -> _dt.date | None:
        try:
            return _dt.datetime.fromisoformat(self.observed_at).date()
        except (TypeError, ValueError):
            return None

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
                         cabin: str | None = None,
                         today: _dt.date | None = None,
                         deconfound_season: bool = True) -> list[CurvePoint]:
        """Observations normalised so different routes and seasons compare.

        Two corrections make this usable on the data the engine can actually
        collect:

        **Reference price.** Dividing by each departure's own minimum only
        works once that departure has completed and has been observed enough
        times. A cross-sectional sweep contributes ONE observation per
        departure, whose own minimum is itself -- every ratio would be
        exactly 1.0 and the fit would learn nothing from a grid sweep. So a
        departure uses its own realised minimum only when it has completed
        with enough coverage; otherwise it is normalised against a
        route-level reference built from the departures that qualify.

        **Seasonality.** Cross-sectional data confounds the days-out effect
        with the departure-date effect -- a departure 300 days out sits in a
        different season than one 7 days out. Dividing the season multiplier
        out here means every consumer gets deconfounded data by construction.
        Sources with synthetic departure dates are exempted, since their
        calendar position is meaningless.
        """
        from .. import seasonality
        from ..models import Trip
        from ..routing import classify

        today = today or _dt.date.today()
        rows = self.series(origin, destination, cabin)
        if not rows:
            return []

        by_departure: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_departure.setdefault(row["depart_date"], []).append(row)

        route_class = classify(Trip(origin=origin, destination=destination,
                                    depart=today))

        def season_for(depart_iso: str, source: str) -> float:
            if not deconfound_season or source in SYNTHETIC_DATE_SOURCES:
                return 1.0
            try:
                depart = _dt.date.fromisoformat(depart_iso)
            except ValueError:
                return 1.0
            multiplier, _ = seasonality.season_multiplier(depart, route_class)
            return multiplier or 1.0

        # Departures good enough to define a reference: completed, and seen
        # often enough that their minimum is plausibly the realised trough.
        qualified: dict[str, float] = {}
        for depart_iso, observations in by_departure.items():
            if len(observations) < MIN_OBSERVATIONS_FOR_REALISED_MIN:
                continue
            try:
                if _dt.date.fromisoformat(depart_iso) > today:
                    continue
            except ValueError:
                continue
            prices = [
                r["price"] / season_for(depart_iso, r["source"])
                for r in observations if r["price"] > 0
            ]
            if prices:
                qualified[depart_iso] = min(prices)

        # Route-level fallback for departures that do not qualify.
        if qualified:
            route_reference = statistics.median(qualified.values())
        else:
            deseasoned = [
                r["price"] / season_for(r["depart_date"], r["source"])
                for r in rows if r["price"] > 0
            ]
            route_reference = min(deseasoned) if deseasoned else 0.0
        if route_reference <= 0:
            return []

        out: list[CurvePoint] = []
        for depart_iso, observations in by_departure.items():
            realised = qualified.get(depart_iso)
            reference = realised if realised else route_reference
            basis = "realised_min" if realised else "route_reference"
            for row in observations:
                if row["price"] <= 0:
                    continue
                price = row["price"] / season_for(depart_iso, row["source"])
                out.append(CurvePoint(
                    days_out=row["days_out"],
                    ratio=price / reference,
                    observed_at=row["observed_at"],
                    source=row["source"],
                    basis=basis,
                ))
        return sorted(out, key=lambda p: p.days_out)
