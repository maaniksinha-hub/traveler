"""Airport registry, backed by OurAirports open data.

The engine previously carried hand-maintained airport lists, which meant
route classification silently degraded for anything not typed out by hand --
unknown codes fell back to long-haul international, and `BOM-MIA` lost its US
DOT rights because Miami was not in the table.

`data/airports.csv` is derived from `OurAirports <https://ourairports.com/data/>`_,
released under the Open Data Commons Public Domain Dedication (PDDL 1.0) --
free to use, redistribute and modify, no attribution required (though it is
offered here anyway, because the project deserves it).

The bundled extract keeps IATA-coded airports with scheduled service, plus
every Indian airport regardless of scheduled status: UDAN strips flip in and
out of service between seasons, and dropping them would defeat the capped-fare
check. 4,085 airports, 81 KB, parsed with the standard library.

Refresh it against upstream with::

    python3 -m traveler.airports --refresh

The hand-maintained tables in ``knowledge.py`` remain as a fallback, so the
engine still works if the data file is missing.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .knowledge import AIRPORT_COUNTRY, INDIAN_AIRPORTS, MAINSTREAM_AIRPORTS

DATA_FILE = Path(__file__).parent / "data" / "airports.csv"

UPSTREAM_URL = (
    "https://raw.githubusercontent.com/davidmegginson/ourairports-data/"
    "main/airports.csv"
)

#: Size classes in the bundled extract.
LARGE, MEDIUM, SMALL = "L", "M", "S"


@dataclass(frozen=True)
class Airport:
    iata: str
    country: str
    size: str
    city: str

    @property
    def is_large(self) -> bool:
        return self.size == LARGE


@lru_cache(maxsize=1)
def _registry() -> dict[str, Airport]:
    """Load the bundled extract once.

    A missing or unreadable file is not fatal -- callers fall back to the
    hand-maintained tables, so the engine degrades rather than breaking.
    """
    if not DATA_FILE.exists():
        return {}
    try:
        with DATA_FILE.open(newline="", encoding="utf-8") as handle:
            return {
                row["iata"]: Airport(row["iata"], row["country"],
                                     row["size"], row.get("city", ""))
                for row in csv.DictReader(handle)
                if row.get("iata")
            }
    except (OSError, csv.Error, KeyError):
        return {}


def lookup(code: str) -> Airport | None:
    return _registry().get(code.strip().upper())


def country_of(code: str) -> str | None:
    """ISO country for an IATA code, registry first, hand table as fallback."""
    normalised = code.strip().upper()
    airport = _registry().get(normalised)
    if airport:
        return airport.country
    if normalised in INDIAN_AIRPORTS:
        return "IN"
    return AIRPORT_COUNTRY.get(normalised)


def is_indian(code: str) -> bool:
    return country_of(code) == "IN"


def is_large(code: str) -> bool:
    """Whether this is a major airport with heavy scheduled service."""
    airport = lookup(code)
    if airport is not None:
        return airport.is_large
    return code.strip().upper() in MAINSTREAM_AIRPORTS


def is_mainstream(code: str) -> bool:
    """Whether a route through here would carry ordinary full-fare service.

    Union of the registry's large airports and the curated list, so upgrading
    to open data adds coverage without discarding hand-checked judgement
    about mid-sized Indian airports that carry mainstream service.
    """
    normalised = code.strip().upper()
    return is_large(normalised) or normalised in MAINSTREAM_AIRPORTS


def coverage() -> dict[str, int]:
    """How much the registry actually knows -- surfaced by the CLI."""
    registry = _registry()
    return {
        "airports": len(registry),
        "countries": len({a.country for a in registry.values()}),
        "indian": sum(1 for a in registry.values() if a.country == "IN"),
    }


def refresh(url: str = UPSTREAM_URL, dest: Path = DATA_FILE) -> int:
    """Re-derive the bundled extract from upstream OurAirports.

    Needs network access and is the only part of this module that does; the
    engine never fetches at runtime.
    """
    try:
        import urllib.request
    except ImportError:  # pragma: no cover
        raise RuntimeError("urllib unavailable")

    keep_types = {
        "large_airport": LARGE,
        "medium_airport": MEDIUM,
        "small_airport": SMALL,
    }

    with urllib.request.urlopen(url, timeout=180) as response:
        text = response.read().decode("utf-8")

    rows = []
    for row in csv.DictReader(text.splitlines()):
        iata = (row.get("iata_code") or "").strip().upper()
        if len(iata) != 3 or not iata.isalpha():
            continue
        size = keep_types.get(row.get("type", ""))
        if size is None:
            continue
        # Indian airports are kept regardless of scheduled_service: UDAN
        # sectors come and go seasonally.
        if row.get("scheduled_service") != "yes" and row.get("iso_country") != "IN":
            continue
        rows.append((iata, row["iso_country"], size,
                     (row.get("municipality") or "").strip()))

    rows.sort()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["iata", "country", "size", "city"])
        writer.writerows(rows)

    _registry.cache_clear()
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="traveler.airports",
        description="Inspect or refresh the bundled airport registry "
                    "(OurAirports, public domain).",
    )
    parser.add_argument("--refresh", action="store_true",
                        help="Re-download from OurAirports and rebuild the extract")
    parser.add_argument("--lookup", metavar="IATA", help="Show one airport")
    args = parser.parse_args(argv)

    if args.refresh:
        count = refresh()
        print(f"Rebuilt {DATA_FILE} with {count:,} airports from OurAirports.")

    if args.lookup:
        airport = lookup(args.lookup)
        if airport is None:
            print(f"{args.lookup.upper()}: not in registry")
            return 1
        print(f"{airport.iata}: {airport.city or '(unknown city)'}, "
              f"{airport.country}, size={airport.size}")
        return 0

    stats = coverage()
    if not stats["airports"]:
        print("Registry empty -- falling back to the hand-maintained tables in "
              "knowledge.py. Run with --refresh to rebuild.")
        return 1
    print(f"{stats['airports']:,} airports across {stats['countries']} "
          f"countries ({stats['indian']} Indian).")
    print("Source: OurAirports, Open Data Commons PDDL 1.0 (public domain).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
