"""The orchestrator: trip in, ranked plan out."""

from __future__ import annotations

import datetime as _dt

from . import options, payments, rights, routing, tactics, timing
from . import airports as airport_registry
from .market import DEFAULT_MODEL, MarketModel
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
    """Whether US DOT protections apply.

    Derived from the country map rather than a hardcoded airport list, which
    previously missed most US gateways -- BOM-MIA silently lost its DOT
    rights while BOM-JFK kept them.
    """
    return any(
        airport_registry.country_of(code) == "US"
        for code in (trip.origin, trip.destination)
    )


def plan_trip(trip: Trip,
              traveller: Traveller | None = None,
              regime: MarketRegime = MarketRegime.RISING,
              today: _dt.date | None = None,
              decision_probability: float = 0.8,
              foreign_pos: bool = False,
              model: MarketModel | None = None) -> Plan:
    """Produce a complete booking strategy for one trip.

    The default regime is RISING, reflecting the 2026 fuel-shock market. The
    default market model is the hand-authored one; pass a ``FittedMarketModel``
    once calibration has run against real observations.
    """
    traveller = traveller or Traveller()
    today = today or _dt.date.today()
    model = model or DEFAULT_MODEL
    days_out = trip.days_out(today)

    route_class = routing.classify(trip)
    season_mult, season_note = model.season_multiplier(trip.depart, route_class)
    urgency_level, timing_note = timing.urgency(
        days_out, route_class, regime, season_mult
    )

    plan = Plan(
        trip=trip,
        route_class=route_class,
        urgency=urgency_level,
        timing_note=timing_note,
        trigger_price_inr=timing.trigger_price(
            trip.baseline_fare, days_out, route_class, season_mult, model
        ),
        season_multiplier=season_mult,
        season_note=season_note,
    )
    plan.fare_verdict = _fare_verdict(trip, plan.trigger_price_inr)

    # --- Tactics -----------------------------------------------------------
    plan.tactics = tactics.build(trip, traveller, route_class)

    # --- Optionality -------------------------------------------------------
    lookin = options.dgca_lookin(trip, route_class, days_out, model)
    if lookin is not None:
        plan.options.append(lookin)

    if urgency_level in (Urgency.HOLD_AND_WATCH, Urgency.BOOK_NOW):
        plan.options.append(
            options.fare_hold(
                trip, route_class, days_out, decision_probability, model
            )
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
        lookin_value_inr=lookin.expected_value.mid if lookin else 0.0,
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
    plan.risks = _risks(trip, traveller, route_class, plan, model)
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
           plan: Plan, model: MarketModel) -> list[str]:
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

    if trip.reference_fare is None:
        out.append(
            "No fare data supplied: option values and the payment stack are "
            "qualitative only. Re-run with --fare and --baseline for numbers."
        )

    out.append(
        f"Model provenance: {model.provenance}. Rankings are more reliable "
        f"than the rupee figures until calibration has run."
    )
    out.append(
        "Pay by credit card in all cases -- chargeback rights are the backstop "
        "for every other risk here."
    )
    return out


def summarise(plan: Plan) -> dict[str, object]:
    """Machine-readable summary, for tests and downstream tooling."""
    fare = plan.trip.reference_fare
    portfolio = tactics.portfolio_estimate(plan.tactics, fare)
    payment = plan.payment
    return {
        "route_class": plan.route_class.value,
        "urgency": plan.urgency.value,
        "days_out": plan.trip.days_out(),
        "trigger_price_inr": plan.trigger_price_inr,
        "fare_verdict": plan.fare_verdict,
        "season_multiplier": plan.season_multiplier,
        "top_tactics": [t.key for t in plan.headline_tactics[:5]],
        "portfolio_saving_inr": None if portfolio is None else {
            "low": round(portfolio.low, 2),
            "mid": round(portfolio.mid, 2),
            "high": round(portfolio.high, 2),
        },
        "payment_channel": payment.channel.value if payment else None,
        "payment_cash_off_inr": payment.cash_off_inr if payment else None,
        "payment_points_value_inr": payment.points_value_inr if payment else None,
        "payment_option_value_inr": (
            payment.option_value_inr - payment.forfeited_option_inr
        ) if payment else None,
    }
