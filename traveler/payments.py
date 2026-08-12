"""Payment-stack optimiser.

Given a fare, a wallet and a booking day, work out the cheapest way to
actually pay. This is a small constrained optimisation, not a lookup: bank
offers are capped, mutually exclusive, and day-of-week gated, and the best
headline percentage frequently loses to a smaller uncapped one.

The optimiser also charges the OTA route for the DGCA window it forfeits, so
channels are compared on net value rather than sticker discount.
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
    charged on that notional interest, plus a processing fee. Foreclosure is
    not modelled here beyond a note, since it is optional.
    """
    notional_interest = fare * (nominal_rate_pct / 100.0) * (tenure_months / 12.0)
    gst = notional_interest * (EMI_GST_PCT / 100.0)
    return gst + EMI_TYPICAL_PROCESSING_FEE_INR


def forex_cost(fare: float, traveller: Traveller, foreign_pos: bool) -> tuple[float, str]:
    """Cost of currency, and which card to use.

    Only relevant when buying from a foreign point of sale -- which is also
    the only situation in which point-of-sale arbitrage pays.
    """
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
        note = (
            f"No card supplied; assuming {markup:.1f}% markup ({cost:,.0f} INR)."
        )
    return cost, note


def optimise(trip: Trip, traveller: Traveller, route_class: RouteClass,
             booking_date: _dt.date | None = None,
             lookin_value_inr: float = 0.0,
             foreign_pos: bool = False) -> PaymentPlan:
    """Choose the payment route with the best net value.

    Compares airline-direct (keeps the DGCA window, earns portal points) with
    the best live OTA offer (bigger sticker discount, forfeits the window).
    """
    fare = trip.observed_fare or trip.baseline_fare
    booking_date = booking_date or _dt.date.today()
    day = booking_date.weekday()

    if fare is None:
        plan = PaymentPlan(channel=Channel.AIRLINE_DIRECT)
        plan.steps.append(
            "No fare supplied -- defaulting to airline-direct, which preserves "
            "the DGCA look-in window and is the safe default under NDC."
        )
        plan.notes.append(
            "Re-run with --fare to get the payment stack costed out."
        )
        return plan

    # --- Option A: airline direct -----------------------------------------
    portal_card = traveller.best_portal_card
    portal_value = 0.0
    direct_steps = ["Book on the airline's own site."]
    if portal_card and portal_card.portal_multiplier > 1.0:
        portal_value = fare * (
            portal_card.travel_reward_pct * portal_card.portal_multiplier / 100.0
        )
        direct_steps.append(
            f"Pay with {portal_card.name} "
            f"({portal_card.portal_multiplier:.0f}x travel earn, "
            f"~{portal_value:,.0f} INR of points)."
        )
    elif portal_card:
        portal_value = fare * portal_card.travel_reward_pct / 100.0
        direct_steps.append(
            f"Pay with {portal_card.name} (~{portal_value:,.0f} INR of points)."
        )
    direct_total = portal_value + lookin_value_inr

    # --- Option B: OTA with the best live bank offer ----------------------
    ota = best_ota_offer(fare, day)
    ota_total = 0.0
    if ota is not None:
        offer, value = ota
        ota_total = value + (portal_value * 0.5)  # portals usually earn less

    # --- Decide ------------------------------------------------------------
    if ota is not None and ota_total > direct_total:
        offer, value = ota
        plan = PaymentPlan(channel=Channel.OTA)
        plan.gross_discount_inr = value
        plan.forfeited_value_inr = lookin_value_inr
        plan.steps.append(
            f"Book on {offer['platform']} with {offer['issuer']}: "
            f"{offer['pct']}% capped at {offer['cap_inr']:,.0f} INR "
            f"-> {value:,.0f} INR off "
            f"(effective {effective_pct(offer, fare):.1f}%)."
        )
        if offer.get("days") is not None:
            plan.notes.append(
                "This offer is day-of-week gated -- confirm it is live before "
                "booking. Offer tables rotate constantly."
            )
        plan.notes.append(
            f"This forfeits the DGCA look-in window "
            f"(worth ~{lookin_value_inr:,.0f} INR). Only correct because your "
            f"plans are firm."
        )
    else:
        plan = PaymentPlan(channel=Channel.AIRLINE_DIRECT)
        plan.gross_discount_inr = direct_total
        plan.steps.extend(direct_steps)
        if ota is not None:
            offer, value = ota
            plan.notes.append(
                f"Best OTA alternative was {offer['platform']} at "
                f"{value:,.0f} INR, which does not beat direct once the "
                f"forfeited DGCA window is priced in."
            )

    # --- Cross-cutting advice ---------------------------------------------
    fx_cost, fx_note = forex_cost(fare, traveller, foreign_pos)
    plan.notes.append(fx_note)
    if foreign_pos:
        plan.notes.append(
            f"Decline dynamic currency conversion -- accepting INR billing "
            f"abroad typically costs a further ~{DCC_PENALTY_PCT:.0f}% "
            f"({fare * DCC_PENALTY_PCT / 100.0:,.0f} INR)."
        )

    emi = emi_true_cost(fare)
    plan.notes.append(
        f"'No-cost' EMI would add roughly {emi:,.0f} INR in GST on notional "
        f"interest plus processing fee, and blocks other coupons. Foreclosure "
        f"costs a further {EMI_FORECLOSURE_PCT:.0f}% + GST. Decline unless the "
        f"cash-flow benefit is worth that."
    )
    plan.notes.append(
        "Check whether a discounted brand voucher exists on SmartBuy/Gyftr: "
        "voucher discount stacks with the platform's own sale, and the voucher "
        "purchase still earns reward points."
    )
    return plan
