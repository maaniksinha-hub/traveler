"""When to book -- the prescriptive layer.

Reads market behaviour from a ``MarketModel`` and turns it into a decision.
All "how do prices move" logic lives in ``market``; this module only decides
what to do about it.

The published studies on optimal booking windows disagree with each other --
that disagreement is itself the finding -- so this produces a band and an
urgency signal rather than a spuriously precise "best day to book".
"""

from __future__ import annotations

import datetime as _dt

from .knowledge import BOOKING_WINDOWS, LAST_MINUTE_CLIFF_DAYS
from .market import DEFAULT_MODEL, MarketModel
from .models import MarketRegime, RouteClass, Urgency


def window_for(route_class: RouteClass, regime: MarketRegime) -> tuple[int, int]:
    """Booking window in days-out, shifted by the market regime.

    A rising market pushes the whole window earlier: waiting costs more than
    it saves when the drift is upward.
    """
    lo, hi = BOOKING_WINDOWS[route_class.value]
    bias = regime.bias_days
    return lo + bias, hi + bias


def urgency(days_out: int, route_class: RouteClass, regime: MarketRegime,
            season_mult: float = 1.0) -> tuple[Urgency, str]:
    """Decide whether to book, hold, or wait -- with the reasoning.

    A peak-season departure compresses the window: the trough that the
    off-peak curve promises often never arrives, so peak trips are pushed
    one step more urgent.
    """
    lo, hi = window_for(route_class, regime)
    cliff = LAST_MINUTE_CLIFF_DAYS[route_class.value]
    peak = season_mult >= 1.10

    if days_out < 0:
        return Urgency.BOOK_NOW, "Departure date is in the past."

    if days_out <= cliff:
        return (
            Urgency.BOOK_NOW,
            f"Inside the {cliff}-day last-minute cliff. Fares rise steeply from "
            f"here; every day of waiting costs more than it can save.",
        )

    if days_out < lo:
        return (
            Urgency.BOOK_NOW,
            f"Past the trough ({lo}-{hi} days out) and approaching the cliff at "
            f"{cliff} days. Book at the first acceptable price.",
        )

    if days_out <= hi:
        note = f"Inside the optimal window ({lo}-{hi} days out)."
        if regime is MarketRegime.RISING:
            note += (
                " Market regime is RISING, so bias toward the early half of the "
                "window and lock rather than wait for a dip."
            )
        if peak:
            return Urgency.BOOK_NOW, note + (
                " Peak-season departure: the usual trough is unlikely to "
                "materialise, so treat this as book-now rather than watch."
            )
        return Urgency.HOLD_AND_WATCH, note

    if days_out <= hi + 60:
        note = (
            f"Earlier than the {lo}-{hi} day window. Set alerts now; expect "
            f"better pricing as inventory is optimised."
        )
        if peak:
            return Urgency.HOLD_AND_WATCH, note + (
                " Peak-season departure, so start watching now rather than "
                "waiting for the window to open."
            )
        return Urgency.WAIT, note

    return (
        Urgency.TOO_EARLY,
        f"Very early ({days_out} days out). Schedules may not be finalised. "
        f"Track, but do not expect the trough yet.",
    )


def trigger_price(baseline_fare: float | None, days_out: int,
                  route_class: RouteClass, season_mult: float = 1.0,
                  model: MarketModel | None = None) -> float | None:
    """The pre-commitment price: book at or below this, no deliberation.

    Derived from the baseline, the position on the curve, and the departure
    date's demand multiplier. Deriving it rather than fixing it means the
    target loosens honestly as departure approaches, instead of anchoring on
    a trough price that is no longer reachable.
    """
    if baseline_fare is None:
        return None
    model = model or DEFAULT_MODEL
    return round(
        baseline_fare
        * model.price_multiplier(days_out, route_class)
        * season_mult,
        2,
    )


def volatility_pct(days_out: int, route_class: RouteClass,
                   model: MarketModel | None = None) -> float:
    """Convenience passthrough so callers need not import ``market``."""
    return (model or DEFAULT_MODEL).volatility_pct(days_out, route_class)


def season_for(depart: _dt.date, route_class: RouteClass,
               model: MarketModel | None = None) -> tuple[float, str | None]:
    """Convenience passthrough for the departure-date demand multiplier."""
    return (model or DEFAULT_MODEL).season_multiplier(depart, route_class)
