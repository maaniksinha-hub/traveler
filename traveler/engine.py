"""The orchestrator: trip in, ranked plan out."""

from __future__ import annotations

import datetime as _dt

from . import options, payments, rights, routing, tactics, timing
from .models import (
    Channel,
    MarketRegime,
    Plan,
    RouteClass,
    Traveller,
    Trip,
    Urgency,
)


def _touches_us(trip: Trip) -> bool:
    us = {"JFK", "EWR", "SFO", "ORD", "IAD", "LAX", "BOS", "SEA", "ATL", "DFW"}
    return trip.origin.upper() in us or trip.destination.upper() in us


def plan_trip(trip: Trip,
              traveller: Traveller | None = None,
              regime: MarketRegime = MarketRegime.RISING,
              today: _dt.date | None = None,
              decision_probability: float = 0.8,
              foreign_pos: bool = False) -> Plan:
    """Produce a complete booking strategy for one trip.

    The default regime is RISING, reflecting the 2026 fuel-shock market. Pass
    a different regime when conditions change -- it shifts every timing
    decision rather than being baked into the curve.
    """
    traveller = traveller or Traveller()
    today = today or _dt.date.today()
    days_out = trip.days_out(today)

    route_class = routing.classify(trip)
    urgency_level, timing_note = timing.urgency(days_out, route_class, regime)

    plan = Plan(
        trip=trip,
        route_class=route_class,
        urgency=urgency_level,
        timing_note=timing_note,
        trigger_price_inr=timing.trigger_price(
            trip.baseline_fare, days_out, route_class
        ),
    )
    plan.fare_verdict = _fare_verdict(trip, plan.trigger_price_inr)

    # --- Tactics -----------------------------------------------------------
    plan.tactics = tactics.build(trip, traveller, route_class)

    # --- Optionality -------------------------------------------------------
    lookin = options.dgca_lookin(trip, route_class, days_out)
    if lookin is not None:
        plan.options.append(lookin)

    if urgency_level in (Urgency.HOLD_AND_WATCH, Urgency.BOOK_NOW):
        plan.options.append(
            options.fare_hold(trip, route_class, days_out, decision_probability)
        )

    award = options.award_placeholder(
        trip, bool(traveller.available_points or traveller.loyalty_programmes)
    )
    if award is not None:
        plan.options.append(award)

    # --- Payment -----------------------------------------------------------
    plan.payment = payments.optimise(
        trip,
        traveller,
        route_class,
        booking_date=today,
        lookin_value_inr=lookin.expected_value_inr if lookin else 0.0,
        foreign_pos=foreign_pos,
    )

    # --- Rights ------------------------------------------------------------
    plan.rights = rights.applicable(
        route_class,
        plan.payment.channel if plan.payment else Channel.AIRLINE_DIRECT,
        days_out,
        touches_us=_touches_us(trip),
    )

    # --- Risks -------------------------------------------------------------
    plan.risks = _risks(trip, traveller, route_class, plan)
    return plan


def _fare_verdict(trip: Trip, trigger: float | None) -> str | None:
    """Compare the fare in hand against the pre-committed trigger price.

    This is the whole point of setting a trigger: it converts an open-ended
    "is this good?" into a decision made before the anchoring set in.
    """
    fare = trip.observed_fare
    if fare is None or trigger is None:
        return None

    delta = trigger - fare
    pct = 100.0 * delta / trigger

    if delta >= 0:
        return (
            f"BUY. The fare in hand ({fare:,.0f} INR) is {pct:.1f}% below your "
            f"trigger of {trigger:,.0f} INR. You pre-committed to this price -- "
            f"book it without further deliberation."
        )
    if pct > -8.0:
        return (
            f"MARGINAL. {fare:,.0f} INR is {abs(pct):.1f}% above your "
            f"{trigger:,.0f} INR trigger. Buy a fare hold and give it a few "
            f"days rather than accepting or walking away now."
        )
    return (
        f"HOLD. {fare:,.0f} INR is {abs(pct):.1f}% above your {trigger:,.0f} "
        f"INR trigger. Keep alerts running and work the ranked tactics below."
    )


def _risks(trip: Trip, traveller: Traveller, route_class: RouteClass,
           plan: Plan) -> list[str]:
    out: list[str] = []

    if trip.flexibility.accepts_self_transfer:
        buffer_h = routing.min_self_transfer_buffer_hours(trip.cabin.is_premium)
        out.append(
            f"Self-transfer accepted: enforce a {buffer_h}h minimum between "
            f"separate tickets, or take an overnight at the gateway."
        )

    if traveller.indian_passport and route_class in (
        RouteClass.LONG_HAUL_INTL, RouteClass.SHORT_HAUL_INTL
    ):
        out.append(
            "Screen every cheap routing for transit-visa requirements on an "
            "Indian passport. Generic metasearch will happily show itineraries "
            "you cannot legally fly."
        )

    if any(t.key == "consolidator" for t in plan.tactics):
        out.append(
            "Consolidator bookings: confirm the 13-digit e-ticket on the "
            "airline's own site within 24 hours. A PNR is not a ticket."
        )

    if trip.flexibility.hard_dates:
        out.append(
            "Hard dates remove every flexibility-based tactic. Consolidators, "
            "fare holds and the DGCA window are your remaining levers."
        )

    if trip.observed_fare is None and trip.baseline_fare is None:
        out.append(
            "No fare data supplied: option values and the payment stack are "
            "qualitative only. Re-run with --fare and --baseline for numbers."
        )

    out.append(
        "Pay by credit card in all cases -- chargeback rights are the backstop "
        "for every other risk here."
    )
    return out


def summarise(plan: Plan) -> dict[str, object]:
    """Machine-readable summary, for tests and downstream tooling."""
    fare = plan.trip.observed_fare or plan.trip.baseline_fare
    return {
        "route_class": plan.route_class.value,
        "urgency": plan.urgency.value,
        "days_out": plan.trip.days_out(),
        "trigger_price_inr": plan.trigger_price_inr,
        "fare_verdict": plan.fare_verdict,
        "top_tactics": [t.key for t in plan.headline_tactics[:5]],
        "portfolio_saving_inr": tactics.portfolio_estimate(plan.tactics, fare),
        "payment_channel": plan.payment.channel.value if plan.payment else None,
        "payment_net_inr": plan.payment.net_discount_inr if plan.payment else None,
    }
