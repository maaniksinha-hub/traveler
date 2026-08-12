"""Route classification and routing-shape tactics."""

from __future__ import annotations

from .knowledge import (
    AIRPORT_COUNTRY,
    INDIAN_AIRPORTS,
    MAINSTREAM_AIRPORTS,
    SHORT_HAUL_COUNTRIES,
)
from .models import RouteClass, Trip


def country_of(airport: str) -> str | None:
    """Best-effort ISO country for an IATA code."""
    code = airport.strip().upper()
    if code in INDIAN_AIRPORTS:
        return "IN"
    return AIRPORT_COUNTRY.get(code)


def classify(trip: Trip) -> RouteClass:
    """Classify a route.

    Unknown airports are treated as long-haul international when one end is
    Indian -- the conservative choice, since it widens booking windows and
    surfaces more checks rather than fewer.
    """
    origin = trip.origin.strip().upper()
    dest = trip.destination.strip().upper()

    origin_in = origin in INDIAN_AIRPORTS
    dest_in = dest in INDIAN_AIRPORTS

    if origin_in and dest_in:
        return RouteClass.DOMESTIC_INDIA
    if not origin_in and not dest_in:
        return RouteClass.FOREIGN_DOMESTIC

    foreign = dest if origin_in else origin
    country = country_of(foreign)
    if country and country in SHORT_HAUL_COUNTRIES:
        return RouteClass.SHORT_HAUL_INTL
    return RouteClass.LONG_HAUL_INTL


def is_udan_candidate(trip: Trip, route_class: RouteClass) -> bool:
    """Whether the UDAN capped-fare check is worth running.

    We cannot verify sector membership offline -- the operational route list
    changes constantly -- so this returns True for the *shape* of trip that
    could qualify and the engine tells the user to check the list.

    The guard requires at least one endpoint outside the mainstream network.
    An earlier version only excluded metro-to-metro pairs, which wrongly
    flagged trunk routes like DEL-JAI where both ends carry full-fare service.
    """
    if route_class is not RouteClass.DOMESTIC_INDIA:
        return False
    endpoints = {trip.origin.upper(), trip.destination.upper()}
    return bool(endpoints - MAINSTREAM_AIRPORTS)


def supports_open_jaw(trip: Trip) -> bool:
    """Open-jaw is worth pricing on any return trip, and is free to check."""
    return trip.is_round_trip or trip.multi_city


def supports_split_ticket(trip: Trip, route_class: RouteClass) -> bool:
    """Split ticketing needs both a connection and tolerance for the risk."""
    if route_class in (RouteClass.DOMESTIC_INDIA, RouteClass.FOREIGN_DOMESTIC):
        return False
    return trip.flexibility.accepts_self_transfer


def supports_positioning(trip: Trip, route_class: RouteClass) -> bool:
    """Positioning pays on long-haul, and needs willingness to change origin."""
    if route_class is not RouteClass.LONG_HAUL_INTL:
        return False
    return trip.flexibility.origin_flex


def min_self_transfer_buffer_hours(cabin_premium: bool = False) -> int:
    """Industry-standard minimum buffer for separate tickets.

    4-6h is the published floor for same-day self-transfer; we return the
    upper end because the downside is forfeiting the onward ticket entirely.
    """
    return 6 if not cabin_premium else 5
