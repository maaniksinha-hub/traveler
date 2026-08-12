"""Daily fare snapshot.

Meant to run from cron or a systemd timer:

    AMADEUS_CLIENT_ID=... AMADEUS_CLIENT_SECRET=... \\
        python3 -m traveler.calibration.snapshot --watchlist watchlist.json

Every run appends to the store. The dataset only becomes useful by
accumulation, so the value of this file is entirely in it running unattended
for months. A watchlist entry that fails is logged and skipped -- one bad
route must not abort the batch.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .sources import AmadeusSource, FareSource, FareSourceError
from .store import DEFAULT_DB, FareStore


@dataclass(frozen=True)
class WatchItem:
    """One itinerary to track over time."""

    origin: str
    destination: str
    depart: _dt.date
    ret: _dt.date | None = None
    cabin: str = "economy"

    @classmethod
    def from_dict(cls, raw: dict) -> "WatchItem":
        return cls(
            origin=raw["origin"],
            destination=raw["destination"],
            depart=_dt.date.fromisoformat(raw["depart"]),
            ret=_dt.date.fromisoformat(raw["return"]) if raw.get("return") else None,
            cabin=raw.get("cabin", "economy"),
        )

    def __str__(self) -> str:
        leg = f"{self.origin}-{self.destination} {self.depart}"
        return f"{leg} ({self.cabin})"


def load_watchlist(path: str | Path) -> list[WatchItem]:
    payload = json.loads(Path(path).read_text())
    return [WatchItem.from_dict(item) for item in payload]


def run_once(items: list[WatchItem], source: FareSource,
             store: FareStore, verbose: bool = True) -> int:
    """Poll every watchlist item once. Returns observations recorded."""
    recorded = 0
    for item in items:
        if item.depart < _dt.date.today():
            if verbose:
                print(f"  skip {item}: departure already past", file=sys.stderr)
            continue
        try:
            observations = source.search(
                item.origin, item.destination, item.depart,
                ret=item.ret, cabin=item.cabin,
            )
        except FareSourceError as exc:
            # One failing route must never abort the batch.
            print(f"  FAIL {item}: {exc}", file=sys.stderr)
            continue

        written = store.record(observations)
        recorded += written
        if verbose:
            cheapest = min((o.price for o in observations), default=None)
            price = f"{cheapest:,.0f}" if cheapest is not None else "no offers"
            print(f"  ok   {item}: {written} new, cheapest {price}")
    return recorded


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="traveler.calibration.snapshot",
        description="Append fare observations to the calibration store.",
    )
    p.add_argument("--watchlist", help="JSON file of itineraries to track")
    p.add_argument("--route", help="Ad-hoc route as ORIGIN-DEST, e.g. DEL-BOM")
    p.add_argument("--depart", help="YYYY-MM-DD (required with --route)")
    p.add_argument("--return", dest="ret", help="YYYY-MM-DD")
    p.add_argument("--cabin", default="economy")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--production", action="store_true",
                   help="Use the production Amadeus host instead of test")
    p.add_argument("--once", action="store_true",
                   help="Poll once and exit (the normal cron mode)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.watchlist:
        items = load_watchlist(args.watchlist)
    elif args.route and args.depart:
        origin, _, destination = args.route.partition("-")
        items = [WatchItem(
            origin=origin, destination=destination,
            depart=_dt.date.fromisoformat(args.depart),
            ret=_dt.date.fromisoformat(args.ret) if args.ret else None,
            cabin=args.cabin,
        )]
    else:
        raise SystemExit("Supply --watchlist, or --route with --depart.")

    from .sources import PRODUCTION_HOST, TEST_HOST
    try:
        source = AmadeusSource(host=PRODUCTION_HOST if args.production else TEST_HOST)
    except FareSourceError as exc:
        raise SystemExit(str(exc)) from exc

    store = FareStore(args.db)
    print(f"Polling {len(items)} itinerar{'y' if len(items) == 1 else 'ies'} "
          f"into {args.db}")
    recorded = run_once(items, source, store)
    print(f"Recorded {recorded} new observations; store now holds {store.count()}.")
    if store.count() < 200:
        print(
            "Note: the curve fit needs a few hundred observations across "
            "multiple departure dates before it will emit a model. Keep this "
            "running daily."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
