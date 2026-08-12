"""Pricing optionality.

The core insight this module encodes: every booking decision is a trade in
options, and most travellers give theirs away without pricing them.

  - A fare hold is a cheap call on the fare.
  - The DGCA 48-hour window is a free put on the booking.
  - An award ticket with free redeposit is a free option on the whole trip.
  - Taking an OTA coupon *sells* the DGCA put for the coupon amount.

Each function below returns an OptionAdvice with the expected value worked
out, so the recommendation is arithmetic rather than instinct.
"""

from __future__ import annotations

from . import timing
from .knowledge import (
    DGCA_LOOKIN_HOURS,
    DGCA_LOOKIN_MIN_DAYS_DOMESTIC,
    DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL,
    FARE_HOLD_PRODUCTS,
)
from .models import OptionAdvice, RouteClass, Trip


def _upside_only_move(fare: float, vol_pct: float) -> float:
    """Expected value of the upward half of a symmetric price move.

    A hold only pays when the price rises, so its value is E[max(0, dP)].
    For a symmetric distribution that is half the mean absolute move; the
    0.4 factor is a deliberately conservative haircut on that.
    """
    return fare * (vol_pct / 100.0) * 0.4


def fare_hold(trip: Trip, route_class: RouteClass,
              days_out: int, decision_probability: float = 0.8) -> OptionAdvice:
    """Should the traveller buy a paid fare hold?

    ``decision_probability`` is the chance they actually go ahead with the
    trip; a hold on a booking you will never make is worth nothing.
    """
    domestic = route_class is RouteClass.DOMESTIC_INDIA
    product = FARE_HOLD_PRODUCTS[
        "indigo_6e_domestic" if domestic else "indigo_6e_international"
    ]
    cost = float(product["cost_inr"]) * max(trip.pax, 1)
    label = str(product["label"])

    fare = trip.observed_fare or trip.baseline_fare
    if fare is None:
        return OptionAdvice(
            instrument=label,
            cost_inr=cost,
            expected_value_inr=0.0,
            recommended=True,
            rationale=(
                f"No fare supplied, so the expected value cannot be computed. "
                f"At {cost:.0f} INR the downside is negligible -- buy the hold "
                f"if you are still deciding."
            ),
        )

    vol = timing.volatility_pct(days_out, route_class)
    ev = _upside_only_move(fare, vol) * decision_probability

    recommended = ev > cost
    ratio = ev / cost if cost else float("inf")
    rationale = (
        f"Fare {fare:,.0f} INR, expected {vol:.1f}% movement over the hold "
        f"window, {decision_probability:.0%} chance of proceeding. Expected "
        f"value {ev:,.0f} INR against {cost:,.0f} INR cost "
        f"({ratio:.1f}x)."
    )
    if not recommended:
        rationale += " Cheap enough to buy anyway if your plans are unsettled."
    return OptionAdvice(label, cost, ev, recommended, rationale)


def dgca_lookin(trip: Trip, route_class: RouteClass,
                days_out: int) -> OptionAdvice | None:
    """Value the free 48-hour cancellation window.

    Returns None when the booking does not qualify, so callers can tell
    "worthless" apart from "unavailable".
    """
    international = route_class is not RouteClass.DOMESTIC_INDIA
    min_days = (
        DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL if international
        else DGCA_LOOKIN_MIN_DAYS_DOMESTIC
    )
    if days_out < min_days:
        return None

    fare = trip.observed_fare or trip.baseline_fare
    vol = timing.volatility_pct(days_out, route_class)
    # The window is a free look: its value is the chance of finding something
    # better within 48h, times the typical improvement.
    ev = 0.0 if fare is None else fare * (vol / 100.0) * 0.25

    rationale = (
        f"Booking direct preserves a free {DGCA_LOOKIN_HOURS}-hour window to "
        f"cancel or amend (departure is {days_out} days out, minimum "
        f"{min_days}). "
    )
    if fare is not None:
        rationale += f"Worth roughly {ev:,.0f} INR as a free look."
    else:
        rationale += "Free, so always worth keeping."
    rationale += " Airline-direct bookings only -- OTA bookings do not qualify."

    return OptionAdvice(
        instrument=f"DGCA {DGCA_LOOKIN_HOURS}h look-in window",
        cost_inr=0.0,
        expected_value_inr=ev,
        recommended=True,
        rationale=rationale,
    )


def award_placeholder(trip: Trip, has_points: bool) -> OptionAdvice | None:
    """Value holding a free-cancellation award seat while hunting cash fares."""
    if not has_points:
        return None
    fare = trip.observed_fare or trip.baseline_fare
    # The placeholder's value is insurance against the cash fare rising past
    # what you would have paid -- conservatively a tenth of the fare.
    ev = 0.0 if fare is None else fare * 0.10
    return OptionAdvice(
        instrument="Award seat as free placeholder",
        cost_inr=0.0,
        expected_value_inr=ev,
        recommended=True,
        rationale=(
            "Major programmes allow free cancellation and mile redeposit before "
            "departure. Hold a confirmed award seat, keep hunting cash fares, "
            "and cancel free if cash wins. Check the programme's redeposit rule "
            "first -- basic awards and non-US-origin itineraries are tighter."
        ),
    )


def coupon_versus_lookin(coupon_inr: float,
                         lookin: OptionAdvice | None) -> str:
    """Resolve the OTA-discount vs DGCA-window trade explicitly.

    This is the decision travellers get wrong most often: a visible coupon
    beats an invisible option in intuition, but frequently not in value.
    """
    if lookin is None:
        return (
            f"No DGCA window applies to this booking, so the {coupon_inr:,.0f} "
            f"INR OTA discount is free money. Take it."
        )
    if coupon_inr > lookin.expected_value_inr:
        return (
            f"Take the OTA discount: {coupon_inr:,.0f} INR beats the "
            f"{lookin.expected_value_inr:,.0f} INR option value of the DGCA "
            f"window. Valid only because your plans are firm."
        )
    return (
        f"Book direct and keep the DGCA window. Its option value "
        f"({lookin.expected_value_inr:,.0f} INR) exceeds the "
        f"{coupon_inr:,.0f} INR OTA discount, and the window also protects "
        f"you against your own plans changing."
    )
