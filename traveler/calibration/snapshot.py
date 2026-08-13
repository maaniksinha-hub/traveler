"""Fare snapshot.

Two collection modes, because the booking curve can be filled along either
axis:

**Longitudinal** (the default) -- poll fixed itineraries repeatedly. One
point per itinerary per run, so the curve fills at one observation per day.
Correct, but slow.

**Cross-sectional** (``--grid``) -- sweep many departure dates for a route in
a single run, producing points across the whole ``days_out`` axis
immediately. This is what turns months of waiting into one afternoon. It
confounds lead time with season, which ``store.normalised_curve`` corrects by
dividing the seasonality multiplier out.

``--harvest-history`` goes further: Google publishes a price series per route,
so one call can contribute months of curve data that was never polled at all.

Meant to run from cron:

    SEARCHAPI_API_KEY=... python3 -m traveler.calibration.snapshot \\
        --watchlist watchlist.json --grid 20 --stride 14 --harvest-history

A watchlist entry that fails is logged and skipped -- one bad route must not
abort the batch.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .sources import (
    FareSource,
    FareSourceError,
    FastFlightsSource,
    GoogleFlightsSource,
)
from .store import DEFAULT_DB, FareStore

#: Metered APIs make an unbounded grid sweep an easy way to burn a month's
#: quota in one run, so the budget is explicit and enforced.
DEFAULT_MAX_CALLS = 100


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

    def shifted(self, days: int) -> "WatchItem":
        """Same itinerary, departure moved out by ``days``.

        The return date moves with it so trip length is preserved -- a grid
        sweep that silently lengthened the trip would be measuring two things
        at once.
        """
        return WatchItem(
            origin=self.origin,
            destination=self.destination,
            depart=self.depart + _dt.timedelta(days=days),
            ret=self.ret + _dt.timedelta(days=days) if self.ret else None,
            cabin=self.cabin,
        )

    def __str__(self) -> str:
        return f"{self.origin}-{self.destination} {self.depart} ({self.cabin})"


@dataclass
class SnapshotResult:
    recorded: int = 0
    calls: int = 0
    failures: int = 0
    skipped: int = 0
    budget_exhausted: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self, store: FareStore) -> str:
        lines = [
            f"Recorded {self.recorded:,} new observations in {self.calls} call(s); "
            f"{self.failures} failure(s), {self.skipped} skipped.",
            f"Store now holds {store.count():,} observations.",
        ]
        if self.budget_exhausted:
            lines.append(
                "Call budget exhausted -- the sweep stopped early. Raise "
                "--max-calls or narrow the grid."
            )
        lines.extend(self.notes)
        return "\n".join(lines)


def load_watchlist(path: str | Path) -> list[WatchItem]:
    payload = json.loads(Path(path).read_text())
    return [WatchItem.from_dict(item) for item in payload]


def expand_grid(items: list[WatchItem], grid: int, stride: int,
                today: _dt.date | None = None) -> list[WatchItem]:
    """Fan each itinerary out across ``grid`` departure dates.

    Duplicates are removed, and departures already in the past are dropped
    rather than wasting a metered call on an unbookable date.
    """
    if grid <= 1:
        return items
    today = today or _dt.date.today()
    seen: set[tuple] = set()
    out: list[WatchItem] = []
    for item in items:
        for step in range(grid):
            candidate = item.shifted(step * stride)
            if candidate.depart <= today:
                continue
            key = (candidate.origin, candidate.destination,
                   candidate.depart, candidate.ret, candidate.cabin)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def run_once(items: list[WatchItem], source: FareSource, store: FareStore,
             verbose: bool = True, max_calls: int = DEFAULT_MAX_CALLS,
             harvest_history: bool = False,
             today: _dt.date | None = None) -> SnapshotResult:
    """Poll every watchlist item once, within the call budget."""
    today = today or _dt.date.today()
    result = SnapshotResult()

    for item in items:
        if result.calls >= max_calls:
            result.budget_exhausted = True
            break
        if item.depart < today:
            result.skipped += 1
            if verbose:
                print(f"  skip {item}: departure already past", file=sys.stderr)
            continue

        try:
            observations = source.search(
                item.origin, item.destination, item.depart,
                ret=item.ret, cabin=item.cabin,
            )
            result.calls += 1
        except FareSourceError as exc:
            result.failures += 1
            result.calls += 1
            print(f"  FAIL {item}: {exc}", file=sys.stderr)
            continue

        written = store.record(observations)
        result.recorded += written

        harvested = 0
        if harvest_history and result.calls < max_calls:
            harvester = getattr(source, "harvest_history", None)
            if callable(harvester):
                try:
                    history = harvester(
                        item.origin, item.destination, item.depart,
                        ret=item.ret, cabin=item.cabin,
                    )
                    result.calls += 1
                    harvested = store.record(history)
                    result.recorded += harvested
                except FareSourceError as exc:
                    result.failures += 1
                    print(f"  WARN {item}: history unavailable ({exc})",
                          file=sys.stderr)

        if verbose:
            cheapest = min((o.price for o in observations), default=None)
            price = f"{cheapest:,.0f}" if cheapest is not None else "no offers"
            extra = f", +{harvested} historical" if harvested else ""
            print(f"  ok   {item}: {written} new, cheapest {price}{extra}")

    if harvest_history and result.recorded and not result.budget_exhausted:
        result.notes.append(
            "History harvesting is best-effort: Google publishes insights "
            "only for routes it considers popular, so thin routes contribute "
            "live offers alone."
        )
    return result


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

    grid = p.add_argument_group("cross-sectional sweep")
    grid.add_argument("--grid", type=int, default=1,
                      help="Departure dates to sweep per itinerary (default 1, "
                           "i.e. longitudinal only)")
    grid.add_argument("--stride", type=int, default=14,
                      help="Days between swept departures (default 14)")
    grid.add_argument("--harvest-history", action="store_true",
                      help="Also pull the source's published price history")
    grid.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                      help=f"Hard ceiling on API calls (default "
                           f"{DEFAULT_MAX_CALLS}). These APIs are metered.")

    p.add_argument("--source", default="fast-flights",
                   choices=["fast-flights", "google-flights"],
                   help="fast-flights is free and keyless (default); "
                        "google-flights needs SEARCHAPI_API_KEY but can also "
                        "harvest published price history")
    p.add_argument("--once", action="store_true",
                   help="Poll once and exit (the normal cron mode)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the call plan and budget, then exit")
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

    items = expand_grid(items, args.grid, args.stride)
    per_item = 2 if (args.harvest_history and args.source == "google-flights") else 1
    planned = min(len(items) * per_item, args.max_calls)

    if args.dry_run:
        print(f"{len(items)} itinerar{'y' if len(items) == 1 else 'ies'} after "
              f"grid expansion; ~{len(items) * per_item} calls needed, budget "
              f"{args.max_calls}, so ~{planned} will run.")
        for item in items[:10]:
            print(f"  {item}")
        if len(items) > 10:
            print(f"  ... and {len(items) - 10} more")
        return 0

    try:
        source = (FastFlightsSource() if args.source == "fast-flights"
                  else GoogleFlightsSource())
    except FareSourceError as exc:
        raise SystemExit(str(exc)) from exc

    if args.source == "fast-flights" and args.harvest_history:
        print("Note: fast-flights exposes no price history; --harvest-history "
              "has no effect. Use --source google-flights for that.",
              file=sys.stderr)

    store = FareStore(args.db)
    print(f"Polling {len(items)} itinerar{'y' if len(items) == 1 else 'ies'} "
          f"into {args.db} (budget {args.max_calls} calls)")
    result = run_once(items, source, store, max_calls=args.max_calls,
                      harvest_history=args.harvest_history)
    print(result.summary(store))
    return 0


if __name__ == "__main__":
    sys.exit(main())
