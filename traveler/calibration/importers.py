"""Seed the store from static datasets.

Live polling cannot produce a calibrated curve for months. A published
dataset can seed one immediately -- provided its limits travel with it, which
is why every import is tagged with a source the fit down-weights and the
provenance string reports.

The EaseMyTrip dataset (Kaggle: shubhambathwal/flight-price-prediction) is
the reference case: 300,261 real Indian domestic rows with a ``days_left``
column, covering the six metros in economy and business.

What it teaches: the cliff and near-trough, ``days_left`` 1-49, at a sample
size live polling will not reach for a year.

What it must never be allowed to appear to teach:

- nothing beyond 49 days out -- ``fit`` enforces this with a coverage guard
  so no far-out coefficient can be derived from it;
- nothing about seasonality -- it was collected in a single 50-day window
  (11 Feb to 31 Mar 2022), so departure dates here are synthetic and are
  excluded from seasonal reasoning;
- nothing about today's market -- 2022 is pre-consolidation, with Go First
  still flying and Vistara not yet merged into Air India.

Recency weighting in ``fit`` is what stops it outvoting live observations.
"""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

from .store import FareObservation, FareStore

#: Tag applied to every row from this dataset.
EASEMYTRIP_SOURCE = "kaggle-easemytrip-2022"

#: Midpoint of the collection window (11 Feb - 31 Mar 2022). Individual rows
#: carry no observation date, only ``days_left``, so a nominal date is used
#: and the departure is derived from it. ``days_out`` -- the only axis the
#: curve fit cares about -- is preserved exactly.
EASEMYTRIP_NOMINAL_OBSERVED = _dt.date(2022, 3, 6)

#: Largest lead time present in the dataset. Recorded so the coverage guard
#: can refuse to extrapolate beyond it.
EASEMYTRIP_MAX_DAYS_OUT = 49

CITY_TO_IATA = {
    "delhi": "DEL",
    "mumbai": "BOM",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "kolkata": "CCU",
    "hyderabad": "HYD",
    "chennai": "MAA",
}

CLASS_TO_CABIN = {
    "economy": "economy",
    "business": "business",
}


class ImportError_(ValueError):
    """Raised when a file does not match the expected schema."""


@dataclass(frozen=True)
class ImportReport:
    path: str
    source: str
    rows_read: int = 0
    rows_imported: int = 0
    rows_skipped: int = 0
    max_days_out: int = 0

    def summary(self) -> str:
        return (
            f"{self.path}: read {self.rows_read:,}, imported "
            f"{self.rows_imported:,}, skipped {self.rows_skipped:,} "
            f"(source={self.source}, coverage 0-{self.max_days_out}d)"
        )


def _require_columns(header: list[str] | None, required: set[str], path: Path) -> None:
    """Fail loudly on a schema mismatch.

    Importing a near-miss schema silently is how a store fills with junk that
    then gets fitted, so this refuses rather than guessing.
    """
    present = {c.strip().lower() for c in (header or [])}
    missing = required - present
    if missing:
        raise ImportError_(
            f"{path} is missing required column(s): {', '.join(sorted(missing))}. "
            f"Found: {', '.join(sorted(present)) or '(no header)'}. "
            f"This importer expects the EaseMyTrip schema; use "
            f"import_csv() with an explicit column mapping for other files."
        )


def import_easemytrip_csv(path: str | Path, store: FareStore,
                          batch_size: int = 5_000) -> ImportReport:
    """Import the Kaggle EaseMyTrip dataset into the fare store.

    The file is not fetched -- Kaggle requires an account and API token, so
    download it yourself and pass the local path.
    """
    path = Path(path)
    if not path.exists():
        raise ImportError_(
            f"{path} does not exist. Download the dataset from "
            f"https://www.kaggle.com/datasets/shubhambathwal/flight-price-prediction "
            f"(Kaggle account required) and pass the CSV path."
        )

    required = {"airline", "source_city", "destination_city", "class",
                "price", "days_left"}
    read = imported = skipped = 0
    max_days = 0
    batch: list[FareObservation] = []

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader.fieldnames, required, path)

        for row in reader:
            read += 1
            observation = _row_to_observation(row)
            if observation is None:
                skipped += 1
                continue
            max_days = max(max_days, observation.days_out)
            batch.append(observation)
            if len(batch) >= batch_size:
                imported += store.record(batch)
                batch.clear()

    if batch:
        imported += store.record(batch)

    return ImportReport(
        path=str(path), source=EASEMYTRIP_SOURCE, rows_read=read,
        rows_imported=imported, rows_skipped=skipped, max_days_out=max_days,
    )


def _row_to_observation(row: dict[str, str]) -> FareObservation | None:
    """Map one CSV row, returning None for anything unusable."""
    origin = CITY_TO_IATA.get((row.get("source_city") or "").strip().lower())
    destination = CITY_TO_IATA.get(
        (row.get("destination_city") or "").strip().lower())
    cabin = CLASS_TO_CABIN.get((row.get("class") or "").strip().lower())
    if not origin or not destination or not cabin or origin == destination:
        return None

    try:
        price = float(row["price"])
        days_left = int(float(row["days_left"]))
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or days_left < 0:
        return None

    observed_at = _dt.datetime.combine(
        EASEMYTRIP_NOMINAL_OBSERVED, _dt.time(12, 0))
    depart = EASEMYTRIP_NOMINAL_OBSERVED + _dt.timedelta(days=days_left)

    return FareObservation(
        origin=origin,
        destination=destination,
        depart_date=depart,
        cabin=cabin,
        price=price,
        currency="INR",
        observed_at=observed_at,
        carrier=(row.get("airline") or "").strip() or None,
        source=EASEMYTRIP_SOURCE,
    )


def import_csv(path: str | Path, store: FareStore, *,
               column_map: dict[str, str], source: str,
               currency: str = "INR") -> ImportReport:
    """Generic CSV importer for datasets other than EaseMyTrip.

    ``column_map`` maps this module's field names (``origin``, ``destination``,
    ``cabin``, ``price``, ``days_left``) onto the file's own column names, so
    a second dataset needs a mapping rather than a second module.
    """
    path = Path(path)
    required_fields = {"origin", "destination", "price", "days_left"}
    missing = required_fields - set(column_map)
    if missing:
        raise ImportError_(
            f"column_map is missing: {', '.join(sorted(missing))}"
        )

    read = imported = skipped = 0
    max_days = 0
    batch: list[FareObservation] = []
    nominal = _dt.date.today()

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader.fieldnames,
                         {v.lower() for v in column_map.values()}, path)
        for row in reader:
            read += 1
            try:
                origin = row[column_map["origin"]].strip().upper()
                destination = row[column_map["destination"]].strip().upper()
                price = float(row[column_map["price"]])
                days_left = int(float(row[column_map["days_left"]]))
            except (KeyError, TypeError, ValueError, AttributeError):
                skipped += 1
                continue
            if price <= 0 or days_left < 0 or origin == destination:
                skipped += 1
                continue

            cabin_col = column_map.get("cabin")
            cabin = (row.get(cabin_col, "economy").strip().lower()
                     if cabin_col else "economy")
            max_days = max(max_days, days_left)
            batch.append(FareObservation(
                origin=origin, destination=destination,
                depart_date=nominal + _dt.timedelta(days=days_left),
                cabin=CLASS_TO_CABIN.get(cabin, "economy"), price=price,
                currency=currency,
                observed_at=_dt.datetime.combine(nominal, _dt.time(12, 0)),
                source=source,
            ))
            if len(batch) >= 5_000:
                imported += store.record(batch)
                batch.clear()

    if batch:
        imported += store.record(batch)
    return ImportReport(path=str(path), source=source, rows_read=read,
                        rows_imported=imported, rows_skipped=skipped,
                        max_days_out=max_days)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .store import DEFAULT_DB

    p = argparse.ArgumentParser(
        prog="traveler.calibration.importers",
        description="Seed the fare store from a static dataset.",
    )
    p.add_argument("csv", help="Path to the dataset CSV")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--dataset", default="easemytrip", choices=["easemytrip"])
    args = p.parse_args(argv)

    store = FareStore(args.db)
    report = import_easemytrip_csv(args.csv, store)
    print(report.summary())
    print(
        f"Store now holds {store.count():,} observations.\n"
        f"This seed is weighted down by age in the fit, and the coverage "
        f"guard prevents it producing coefficients beyond "
        f"{report.max_days_out} days out."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
