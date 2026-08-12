"""When to book.

Models expected fare as a multiple of the trough price, as a function of days
before departure. The curve shape is: an early plateau (mild premium), a
trough (the booking window), then a steep last-minute cliff.

The published studies disagree with each other -- that disagreement is the
finding, so this deliberately produces a *band* and an urgency signal rather
than a single "best day".
"""

from __future__ import annotations

from .knowledge import BOOKING_WINDOWS, LAST_MINUTE_CLIFF_DAYS
from .models import MarketRegime, RouteClass, Urgency


def window_for(route_class: RouteClass, regime: MarketRegime) -> tuple[int, int]:
    """Booking window in days-out, shifted by the market regime.

    A rising market pushes the whole window earlier: waiting costs more than
    it saves when the drift is upward.
    """
    lo, hi = BOOKING_WINDOWS[route_class.value]
    bias = regime.bias_days
    return lo + bias, hi + bias


def price_multiplier(days_out: int, route_class: RouteClass) -> float:
    """Expected fare as a multiple of the trough price.

    Piecewise and intentionally smooth-ish; the absolute values matter less
    than the ordering, which is what drives the urgency decision.
    """
    lo, hi = BOOKING_WINDOWS[route_class.value]
    cliff = LAST_MINUTE_CLIFF_DAYS[route_class.value]

    if days_out < 0:
        return float("inf")
    if days_out <= cliff:
        # Steep rise into departure. At 0 days out, roughly 2x the trough.
        ratio = days_out / max(cliff, 1)
        return 2.0 - 0.75 * ratio          # 2.00 -> 1.25
    if days_out < lo:
        # Between the cliff and the trough: mild premium.
        span = max(lo - cliff, 1)
        ratio = (days_out - cliff) / span
        return 1.25 - 0.25 * ratio          # 1.25 -> 1.00
    if days_out <= hi:
        return 1.0                          # the trough
    # Far out: inventory not yet optimised, mild premium that grows slowly.
    excess = days_out - hi
    return min(1.0 + 0.0015 * excess, 1.20)


def urgency(days_out: int, route_class: RouteClass, regime: MarketRegime) -> tuple[Urgency, str]:
    """Decide whether to book, hold, or wait -- with the reasoning."""
    lo, hi = window_for(route_class, regime)
    cliff = LAST_MINUTE_CLIFF_DAYS[route_class.value]

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
        return Urgency.HOLD_AND_WATCH, note

    if days_out <= hi + 60:
        return (
            Urgency.WAIT,
            f"Earlier than the {lo}-{hi} day window. Set alerts now; expect "
            f"better pricing as inventory is optimised.",
        )

    return (
        Urgency.TOO_EARLY,
        f"Very early ({days_out} days out). Schedules may not be finalised. "
        f"Track, but do not expect the trough yet.",
    )


def trigger_price(baseline_fare: float | None, days_out: int,
                  route_class: RouteClass) -> float | None:
    """The pre-commitment price: book at or below this, no deliberation.

    Deriving it from the baseline and the current position on the curve means
    the target loosens honestly as departure approaches, instead of anchoring
    on a trough price that is no longer reachable.
    """
    if baseline_fare is None:
        return None
    return round(baseline_fare * price_multiplier(days_out, route_class), 2)


def volatility_pct(days_out: int, route_class: RouteClass) -> float:
    """Rough expected fare movement over the next few days, as a percentage.

    Feeds the option-pricing model. Volatility grows sharply as departure
    approaches, which is exactly when buying a fare hold pays.
    """
    cliff = LAST_MINUTE_CLIFF_DAYS[route_class.value]
    if days_out <= 0:
        return 0.0
    if days_out <= cliff:
        # Up to ~12% swing inside the cliff.
        return 12.0 * (1.0 - days_out / (2.0 * max(cliff, 1)))
    if days_out <= 90:
        return 4.0
    return 2.5
