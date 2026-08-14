"""Real flight options for display, as opposed to fare observations for fitting.

``calibration.sources`` already talks to fast-flights, but it collapses every
itinerary into a :class:`~traveler.calibration.store.FareObservation`, which is
price-only on purpose: it feeds a curve fitter that has no use for departure
times. Showing somebody which flight to book needs the fields that parser
throws away, so this module keeps them instead of widening the calibration
record to serve two unrelated jobs.

Free and keyless, same source as ``--source fastflights``::

    pip install fast-flights

It is a scraper. There is no contract and no SLA, so every caller has to
handle :class:`FlightLookupError` rather than assume results.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from .calibration.sources import _coerce_price

#: Seconds between calls. Matches ``FastFlightsSource.delay_seconds``: nothing
#: else throttles this source, so the courtesy is ours to keep.
DELAY_SECONDS = 1.5


class FlightLookupError(RuntimeError):
    """The lookup failed. Callers must degrade, never fabricate a flight."""


@dataclass(frozen=True)
class FlightOption:
    """One bookable itinerary, with the detail a person needs to choose."""

    price_inr: float
    airlines: tuple[str, ...] = ()
    depart_at: _dt.datetime | None = None
    arrive_at: _dt.datetime | None = None
    duration_minutes: int | None = None
    stops: int = 0

    @property
    def airline_label(self) -> str:
        if not self.airlines:
            return "Airline not listed"
        if len(self.airlines) == 1:
            return self.airlines[0]
        return f"{self.airlines[0]} +{len(self.airlines) - 1}"

    @property
    def duration_label(self) -> str | None:
        if self.duration_minutes is None:
            return None
        hours, minutes = divmod(self.duration_minutes, 60)
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"

    @property
    def stops_label(self) -> str:
        if self.stops <= 0:
            return "Non-stop"
        return "1 stop" if self.stops == 1 else f"{self.stops} stops"

    def as_dict(self) -> dict:
        return {
            "price_inr": self.price_inr,
            "airline_label": self.airline_label,
            "depart_hhmm": self.depart_at.strftime("%H:%M") if self.depart_at else None,
            "arrive_hhmm": self.arrive_at.strftime("%H:%M") if self.arrive_at else None,
            "duration_label": self.duration_label,
            "stops": self.stops,
            "stops_label": self.stops_label,
            # True when the itinerary lands on a later calendar day than it
            # left, which changes what an arrival time means to the reader.
            "arrives_next_day": bool(
                self.depart_at and self.arrive_at
                and self.arrive_at.date() > self.depart_at.date()
            ),
        }


def _to_datetime(raw) -> _dt.datetime | None:
    """Build a datetime from fast-flights' ``SimpleDatetime``.

    Scraped input, so every field is treated as absent until proven otherwise.
    """
    date_parts = getattr(raw, "date", None)
    time_parts = getattr(raw, "time", None) or (0, 0)
    try:
        year, month, day = (int(p) for p in date_parts)
        hour, minute = (int(p) for p in time_parts)
        return _dt.datetime(year, month, day, hour, minute)
    except (TypeError, ValueError):
        return None


def parse_options(result, limit: int = 6) -> list[FlightOption]:
    """Normalise a fast-flights result into display options.

    Pure, so fixtures can drive it without touching the network. Anything
    unpriced or unparseable is dropped rather than rendered as a gap.
    """
    # ResultList subclasses list, so the itineraries are the result itself.
    # Older releases wrapped them in ``.flights``; both shapes are accepted.
    if isinstance(result, (list, tuple)):
        entries = list(result)
    else:
        entries = list(getattr(result, "flights", None) or [])

    out: list[FlightOption] = []
    for entry in entries:
        price = _coerce_price(getattr(entry, "price", None))
        if price is None:
            continue

        legs = list(getattr(entry, "flights", None) or [])
        depart_at = _to_datetime(getattr(legs[0], "departure", None)) if legs else None
        arrive_at = _to_datetime(getattr(legs[-1], "arrival", None)) if legs else None

        if depart_at and arrive_at and arrive_at >= depart_at:
            duration = int((arrive_at - depart_at).total_seconds() // 60)
        else:
            # No usable clock times. Leg durations miss layovers, so this is a
            # floor rather than a total, and is only used when nothing better
            # exists.
            per_leg = [getattr(leg, "duration", None) for leg in legs]
            usable = [int(d) for d in per_leg if isinstance(d, (int, float))]
            duration = sum(usable) or None

        airlines = getattr(entry, "airlines", None) or []
        out.append(FlightOption(
            price_inr=price,
            airlines=tuple(str(a) for a in airlines if a),
            depart_at=depart_at,
            arrive_at=arrive_at,
            duration_minutes=duration,
            stops=max(len(legs) - 1, 0),
        ))
        if len(out) >= limit:
            break

    return sorted(out, key=lambda o: o.price_inr)


def search_flights(origin: str, destination: str, depart: _dt.date,
                   ret: _dt.date | None = None, cabin: str = "economy",
                   adults: int = 1, limit: int = 6,
                   proxy: str | None = None) -> list[FlightOption]:
    """Look up real flights. Raises :class:`FlightLookupError` on any failure."""
    import time

    try:
        from fast_flights import (  # type: ignore[import-not-found]
            FlightQuery,
            Passengers,
            create_query,
            get_flights,
        )
    except ImportError as exc:
        raise FlightLookupError(
            "fast-flights is not installed. It is free and keyless:\n"
            "    pip install fast-flights"
        ) from exc

    legs = [FlightQuery(date=depart.isoformat(),
                        from_airport=origin.upper(),
                        to_airport=destination.upper())]
    if ret:
        legs.append(FlightQuery(date=ret.isoformat(),
                                from_airport=destination.upper(),
                                to_airport=origin.upper()))

    query = create_query(
        flights=legs,
        seat=cabin.replace("_", "-"),
        trip="round-trip" if ret else "one-way",
        passengers=Passengers(adults=adults),
        currency="INR",
    )

    try:
        result = get_flights(query, proxy=proxy)
    except Exception as exc:  # the library raises its own exception tree
        raise FlightLookupError(
            f"Could not reach the flight search for {origin}-{destination} "
            f"on {depart}: {type(exc).__name__}"
        ) from exc
    finally:
        # Applied even on failure, so a broken route cannot turn into a tight
        # retry loop against the upstream.
        time.sleep(DELAY_SECONDS)

    return parse_options(result, limit=limit)
