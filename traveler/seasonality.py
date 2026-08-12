"""Departure-date demand effects.

Distinct from, and composed with, the days-out effect in ``market``. A
December 24 departure and a February 24 departure sitting the same distance
from today are not the same booking, and the first version of this engine
treated them identically.

Sensitivity is scaled by route class: festivals move Indian domestic demand
far more than they move long-haul.
"""

from __future__ import annotations

import datetime as _dt

from .knowledge import (
    FESTIVAL_PEAKS,
    LAST_CALENDARED_YEAR,
    RECURRING_PEAKS,
    SEASON_SENSITIVITY,
)
from .models import RouteClass


def _in_window(day: _dt.date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    """Whether a date falls in a (month, day) window, handling year wrap."""
    start_md = (start[0], start[1])
    end_md = (end[0], end[1])
    here = (day.month, day.day)
    if start_md <= end_md:
        return start_md <= here <= end_md
    # Window wraps the new year, e.g. Dec 18 -> Jan 5.
    return here >= start_md or here <= end_md


def peaks_for(depart: _dt.date) -> list[tuple[float, str]]:
    """Every peak window this departure date falls inside."""
    hits: list[tuple[float, str]] = []
    for start, end, mult, label in RECURRING_PEAKS:
        if _in_window(depart, start, end):
            hits.append((mult, label))
    for start, end, mult, label in FESTIVAL_PEAKS.get(depart.year, []):
        if _in_window(depart, start, end):
            hits.append((mult, label))
    return hits


def season_multiplier(depart: _dt.date,
                      route_class: RouteClass) -> tuple[float, str | None]:
    """Demand multiplier for a departure date, plus a human explanation.

    Overlapping peaks take the maximum rather than compounding: Diwali
    falling inside a school holiday is one peak, not a 1.4 x 1.3 = 1.82x
    monster.
    """
    hits = peaks_for(depart)
    sensitivity = SEASON_SENSITIVITY[route_class.value]

    if not hits:
        note = None
        if depart.year > LAST_CALENDARED_YEAR:
            note = (
                f"No festival calendar for {depart.year} (calendared through "
                f"{LAST_CALENDARED_YEAR}); recurring peaks only. Extend "
                f"FESTIVAL_PEAKS in knowledge.py."
            )
        return 1.0, note

    raw_mult, label = max(hits, key=lambda h: h[0])
    # Scale the *excess* over 1.0 by route sensitivity, so a 1.40 domestic
    # peak becomes 1.20 on long-haul rather than being dropped entirely.
    effective = 1.0 + (raw_mult - 1.0) * sensitivity

    labels = ", ".join(sorted({lbl for _, lbl in hits}))
    note = (
        f"Departure falls in a demand peak ({labels}): expect roughly "
        f"{(effective - 1.0) * 100:.0f}% above off-peak pricing on this route "
        f"class. Book earlier than the normal window and expect the trough "
        f"never to arrive."
    )
    return round(effective, 3), note


def is_peak(depart: _dt.date, route_class: RouteClass, threshold: float = 1.10) -> bool:
    """Whether this departure is peak enough to change booking behaviour."""
    mult, _ = season_multiplier(depart, route_class)
    return mult >= threshold
