"""Payment-stack optimiser.

Given a fare, a wallet and a booking day, work out the cheapest way to
actually pay. This is a small constrained optimisation, not a lookup: bank
offers are capped, mutually exclusive, and day-of-week gated, and the best
headline percentage frequently loses to a smaller uncapped one.

Three kinds of value are tracked **separately and never summed** into a
headline: cash off the fare, a speculative valuation of points earned, and
the worth of an option kept or surrendered. They are only combined inside
``PaymentPlan.comparable_total`` for the purpose of ranking channels, with
explicit weights.
"""

from __future__ import annotations

import datetime as _dt

from .knowledge import (
    DCC_PENALTY_PCT,
    DEFAULT_FOREX_MARKUP_PCT,
    EMI_FORECLOSURE_PCT,
    EMI_GST_PCT,
    EMI_TYPICAL_PROCESSING_FEE_INR,
    OTA_OFFERS,
)
from .models import Channel, PaymentPlan, RouteClass, Traveller, Trip

#: OTA bookings usually earn the card's ordinary travel rate rather than the
#: issuer-portal accelerator, since the accelerator requires the issuer's own
#: portal. Modelled as "no portal multiplier" rather than a fudge factor.
OTA_USES_PORTAL = False


def _applicable_offers(booking_day: int) -> list[dict[str, object]]:
    """Offers live on a given weekday (Monday=0)."""
    live = []
    for offer in OTA_OFFERS:
        days = offer.get("days")
        if days is None or booking_day in days:  # type: ignore[operator]
            live.append(offer)
    return live


def _offer_value(offer: dict[str, object], fare: float) -> float:
    """Discount actually delivered, after the cap bites."""
    pct = float(offer["pct"])           # type: ignore[arg-type]
    cap = float(offer["cap_inr"])       # type: ignore[arg-type]
    return min(fare * pct / 100.0, cap)


def effective_pct(offer: dict[str, object], fare: float) -> float:
    """The honest percentage after the cap -- often far below the headline."""
    if fare <= 0:
        return 0.0
    return 100.0 * _offer_value(offer, fare) / fare


def best_ota_offer(fare: float, booking_day: int) -> tuple[dict[str, object], float] | None:
    """Pick the offer that delivers the most rupees, not the biggest headline."""
    live = _applicable_offers(booking_day)
    if not live:
        return None
    best = max(live, key=lambda o: _offer_value(o, fare))
    return best, _offer_value(best, fare)


def emi_true_cost(fare: float, tenure_months: int = 3,
                  nominal_rate_pct: float = 14.0) -> float:
    """What 'no-cost' EMI actually costs.

    The interest does not vanish -- the bank books it internally and GST is
    charged on that notional interest, plus a processing fee.
    """
    notional_interest = fare * (nominal_rate_pct / 100.0) * (tenure_months / 12.0)
    gst = notional_interest * (EMI_GST_PCT / 100.0)
    return gst + EMI_TYPICAL_PROCESSING_FEE_INR


def forex_cost(fare: float, traveller: Traveller, foreign_pos: bool) -> tuple[float, str]:
    """Cost of currency, and which card to use."""
    if not foreign_pos:
        return 0.0, "Domestic point of sale: no forex markup applies."
    card = traveller.best_forex_card
    markup = card.forex_markup_pct if card else DEFAULT_FOREX_MARKUP_PCT
    cost = fare * markup / 100.0
    if card and card.is_zero_forex:
        note = f"Use {card.name} (zero forex markup) -- saves the usual 2-3.5%."
    elif card:
        note = (
            f"Best available card is {card.name} at {markup:.1f}% markup "
            f"({cost:,.0f} INR). A zero-forex card would save this entirely."
        )
    else:
        note = f"No card supplied; assuming {markup:.1f}% markup ({cost:,.0f} INR)."
    return cost, note


def _reward_note(traveller: Traveller, fare: float, via_portal: bool) -> tuple[float, str | None]:
    """Realised points value plus an explanation of how it was derived."""
    card = traveller.best_reward_card(fare)
    if card is None or card.programme is None:
        return 0.0, None

    prog = card.programme
    points = prog.points_earned(fare, via_portal)
    value = prog.value_inr(fare, via_portal)
    where = "issuer portal" if via_portal else "direct/OTA rate"

    note = (
        f"Pay with {card.name}: {points:,.0f} points at {where}, worth about "
        f"{value:,.0f} INR after a {prog.realization_rate:.0%} realisation "
        f"haircut at {prog.point_value_inr:.2f} INR/point."
    )
    if prog.cap_binds(fare, via_portal):
        note += (
            f" The {prog.monthly_points_cap:,} point monthly cap binds on this "
            f"booking -- earning above it is forfeited."
        )
    return value, note


def optimise(trip: Trip, traveller: Traveller, route_class: RouteClass,
             booking_date: _dt.date | None = None,
             lookin_value_inr: float = 0.0,
             foreign_pos: bool = False) -> PaymentPlan:
    """Choose the payment route with the best net value.

    Compares airline-direct (keeps the DGCA window, earns portal points) with
    the best live OTA offer (bigger sticker discount, forfeits the window).
    """
    fare = trip.reference_fare
    booking_date = booking_date or _dt.date.today()
    day = booking_date.weekday()

    if fare is None:
        plan = PaymentPlan(channel=Channel.AIRLINE_DIRECT)
        plan.steps.append(
            "No fare supplied -- defaulting to airline-direct, which preserves "
            "the DGCA look-in window and is the safe default under NDC."
        )
        plan.notes.append("Re-run with --fare to get the payment stack costed out.")
        return plan

    # --- Option A: airline direct (issuer portal accelerator available) ----
    direct_points, direct_note = _reward_note(traveller, fare, via_portal=True)
    direct_plan = PaymentPlan(
        channel=Channel.AIRLINE_DIRECT,
        points_value_inr=direct_points,
        option_value_inr=lookin_value_inr,
    )
    direct_plan.steps.append("Book on the airline's own site.")
    if direct_note:
        direct_plan.steps.append(direct_note)

    # --- Option B: OTA with the best live bank offer -----------------------
    ota = best_ota_offer(fare, day)
    ota_plan: PaymentPlan | None = None
    if ota is not None:
        offer, cash = ota
        ota_points, ota_note = _reward_note(traveller, fare, via_portal=OTA_USES_PORTAL)
        ota_plan = PaymentPlan(
            channel=Channel.OTA,
            cash_off_inr=cash,
            points_value_inr=ota_points,
            forfeited_option_inr=lookin_value_inr,
        )
        ota_plan.steps.append(
            f"Book on {offer['platform']} with {offer['issuer']}: "
            f"{offer['pct']}% capped at {offer['cap_inr']:,.0f} INR "
            f"-> {cash:,.0f} INR off "
            f"(effective {effective_pct(offer, fare):.1f}%)."
        )
        if ota_note:
            ota_plan.steps.append(ota_note)
        if offer.get("days") is not None:
            ota_plan.notes.append(
                "This offer is day-of-week gated -- confirm it is live before "
                "booking. Offer tables rotate constantly."
            )
        ota_plan.notes.append(
            f"This forfeits the DGCA look-in window (worth ~"
            f"{lookin_value_inr:,.0f} INR). Only correct because your plans "
            f"are firm."
        )

    # --- Decide -------------------------------------------------------------
    if ota_plan is not None and ota_plan.comparable_total() > direct_plan.comparable_total():
        plan = ota_plan
        plan.notes.append(
            f"Chosen over direct on comparable total: "
            f"{ota_plan.comparable_total():,.0f} vs "
            f"{direct_plan.comparable_total():,.0f} INR."
        )
    else:
        plan = direct_plan
        if ota_plan is not None:
            plan.notes.append(
                f"Best OTA alternative delivered {ota_plan.cash_off_inr:,.0f} INR "
                f"cash, which does not beat direct once portal points and the "
                f"forfeited DGCA window are priced in "
                f"({ota_plan.comparable_total():,.0f} vs "
                f"{direct_plan.comparable_total():,.0f} INR)."
            )

    # --- Cross-cutting advice ----------------------------------------------
    _, fx_note = forex_cost(fare, traveller, foreign_pos)
    plan.notes.append(fx_note)
    if foreign_pos:
        plan.notes.append(
            f"Decline dynamic currency conversion -- accepting INR billing "
            f"abroad typically costs a further ~{DCC_PENALTY_PCT:.0f}% "
            f"({fare * DCC_PENALTY_PCT / 100.0:,.0f} INR)."
        )

    plan.notes.append(
        f"'No-cost' EMI would add roughly {emi_true_cost(fare):,.0f} INR in GST "
        f"on notional interest plus processing fee, and blocks other coupons. "
        f"Foreclosure costs a further {EMI_FORECLOSURE_PCT:.0f}% + GST. Decline "
        f"unless the cash-flow benefit is worth that."
    )
    plan.notes.append(
        "Check whether a discounted brand voucher exists on SmartBuy/Gyftr: "
        "voucher discount stacks with the platform's own sale, and the voucher "
        "purchase still earns reward points."
    )
    return plan
