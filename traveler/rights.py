"""Passenger rights that carry monetary value.

These are levers, not trivia. A significant schedule change converts a cheap
non-refundable ticket into a free rebooking or a cash refund -- which is why
booking early carries an embedded option most travellers never exercise.
"""

from __future__ import annotations

from .knowledge import (
    DGCA_LOOKIN_HOURS,
    DGCA_LOOKIN_MIN_DAYS_DOMESTIC,
    DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL,
    DGCA_REFUND_DAYS_AGENT,
    DGCA_REFUND_DAYS_DIRECT,
    DOT_SIGNIFICANT_DELAY_DOMESTIC_H,
    DOT_SIGNIFICANT_DELAY_INTERNATIONAL_H,
)
from .models import Channel, RouteClass


def applicable(route_class: RouteClass, channel: Channel,
               days_out: int, touches_us: bool = False) -> list[str]:
    """The rights that apply to this specific booking."""
    out: list[str] = []
    international = route_class is not RouteClass.DOMESTIC_INDIA
    min_days = (
        DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL if international
        else DGCA_LOOKIN_MIN_DAYS_DOMESTIC
    )

    if channel is Channel.AIRLINE_DIRECT and days_out >= min_days:
        out.append(
            f"DGCA look-in: free cancellation or amendment within "
            f"{DGCA_LOOKIN_HOURS}h of booking (departure must be >= {min_days} "
            f"days away). Use it as a free price lock while you keep watching."
        )
    elif channel is not Channel.AIRLINE_DIRECT:
        out.append(
            f"No DGCA look-in window: it applies to airline-direct bookings "
            f"only. Agent/OTA cancellation policy governs instead."
        )
    else:
        out.append(
            f"Departure is inside {min_days} days, so the DGCA look-in window "
            f"does not apply to this booking."
        )

    out.append(
        "Schedule change: if the airline significantly retimes or cancels, you "
        "may choose free rebooking OR a full refund to original payment -- not "
        "a credit shell. Do not passively accept a retiming; a better flight "
        "is often available at no cost."
    )
    out.append(
        f"Refund timelines: {DGCA_REFUND_DAYS_DIRECT} working days for direct "
        f"card/UPI/net-banking bookings, up to {DGCA_REFUND_DAYS_AGENT} working "
        f"days via an agent or OTA."
    )

    if touches_us:
        out.append(
            f"US DOT: 'significant change' is >= "
            f"{DOT_SIGNIFICANT_DELAY_DOMESTIC_H}h domestic or >= "
            f"{DOT_SIGNIFICANT_DELAY_INTERNATIONAL_H}h international. Refunds "
            f"are automatic to original payment if you decline the rebooking. "
            f"A separate 24h free-cancellation rule also applies."
        )

    out.append(
        "Fare transparency: since March 2026 all non-government fees, including "
        "convenience fees, must be shown in the initial fare quote. Compare "
        "all-in totals, not headline fares."
    )
    return out


def schedule_change_playbook() -> list[str]:
    """What to actually do when the retiming email arrives."""
    return [
        "Do not click 'accept' on the airline's proposed alternative.",
        "Check whether the change crosses the significant-change threshold.",
        "If it does, ask for the flight you actually want -- free rebooking is "
        "your choice, not the airline's suggestion.",
        "If nothing suits, take the full refund to original payment and rebook.",
        "Escalate in writing citing the rule if the first agent refuses.",
    ]
