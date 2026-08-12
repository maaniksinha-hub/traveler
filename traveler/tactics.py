"""Tactic selection.

Each tactic carries a prior expected saving and a hit probability conditioned
on the trip. Ranking by expected value keeps attention on the few levers that
matter for *this* booking instead of a generic checklist.
"""

from __future__ import annotations

from . import routing
from .knowledge import CATEGORY_FARES, TACTIC_PRIORS, UDAN_FARE_CAP_INR
from .models import Estimate, RouteClass, Tactic, Traveller, Trip
from .models import Channel


def _p(key: str) -> float:
    return TACTIC_PRIORS[key]


def build(trip: Trip, traveller: Traveller, route_class: RouteClass) -> list[Tactic]:
    """Assemble every tactic that plausibly applies, with scores."""
    flex = trip.flexibility
    out: list[Tactic] = []

    # --- Consolidator / net fares -----------------------------------------
    if route_class is RouteClass.LONG_HAUL_INTL:
        # Pays most on premium cabins and fixed dates -- the case where every
        # flexibility-based tactic fails.
        prob = 0.55 if trip.cabin.is_premium else 0.35
        if flex.hard_dates:
            prob += 0.15
        out.append(Tactic(
            key="consolidator",
            title="Consolidator / net fares",
            detail=(
                "Contract fares on hard-to-fill long-haul inventory, discounted "
                "30-60% and contractually barred from online display. Reachable "
                "only through an agent with GDS access. This is the one lever "
                "that works when you have no flexibility at all."
            ),
            expected_saving_pct=_p("consolidator"),
            hit_probability=min(prob, 0.8),
            channel=Channel.CONSOLIDATOR,
            exclusivity_group="channel",
            risks=(
                "Verify a 13-digit e-ticket number, not just a PNR.",
                "Pay by credit card; never bank transfer.",
                "Expect restrictive change/refund rules.",
            ),
        ))

    # --- Award redemption --------------------------------------------------
    if traveller.available_points > 0 or traveller.loyalty_programmes:
        prob = 0.5 if trip.cabin.is_premium else 0.3
        out.append(Tactic(
            key="award_redemption",
            title="Award redemption",
            detail=(
                "Price the award alongside cash every time. Prefer programmes "
                "that do not pass fuel surcharges; with ATF elevated the "
                "surcharge can exceed the mileage saving. Use seats.aero or "
                "point.me to find space, and set alerts -- premium space "
                "appears and vanishes within hours."
            ),
            expected_saving_pct=_p("award_redemption"),
            hit_probability=prob,
            channel=Channel.AWARD,
            exclusivity_group="channel",
            risks=("Check redeposit rules before booking as a placeholder.",),
        ))

    # --- Error fares / deal services ---------------------------------------
    if flex.score > 0.4 and flex.can_book_fast:
        out.append(Tactic(
            key="error_fare",
            title="Error / mistake fares",
            detail=(
                "Misfiled fares, currency errors and unloaded surcharges. "
                "Requires genuine flexibility and the ability to commit within "
                "hours. Book direct with the airline, pay in full, and do not "
                "buy non-refundable hotels for 72 hours."
            ),
            expected_saving_pct=_p("error_fare"),
            hit_probability=0.15 * flex.score,
            channel=Channel.AIRLINE_DIRECT,
            exclusivity_group="channel",
            risks=("The airline may cancel and refund rather than honour it.",),
        ))

    # --- Routing shape -----------------------------------------------------
    if routing.supports_open_jaw(trip):
        out.append(Tactic(
            key="open_jaw",
            title="Open-jaw / multi-city pricing",
            detail=(
                "Open-jaws price under round-trip rules, so each half costs "
                "about half a round-trip while one-ways carry a yield premium. "
                "Free to check -- it is a button in Google Flights -- and it "
                "also removes the backtrack leg. Price it both ways every time."
            ),
            expected_saving_pct=_p("open_jaw"),
            hit_probability=0.45,
        ))

    if routing.supports_split_ticket(trip, route_class):
        out.append(Tactic(
            key="split_ticket",
            title="Split ticketing / self-transfer",
            detail=(
                f"Separate tickets often undercut the through-fare by 25-40%. "
                f"Allow at least "
                f"{routing.min_self_transfer_buffer_hours(trip.cabin.is_premium)}"
                f"h between tickets, or an overnight at the gateway."
            ),
            expected_saving_pct=_p("split_ticket"),
            hit_probability=0.4,
            exclusivity_group="routing",
            risks=(
                "The onward carrier has no obligation if the feeder is late.",
                "Check transit-visa rules for your passport.",
                "Bags usually must be re-checked.",
            ),
        ))

    if routing.supports_positioning(trip, route_class):
        out.append(Tactic(
            key="positioning",
            title="Positioning flight",
            detail=(
                "Start the long-haul from a cheaper gateway. Metro origins are "
                "frequently far cheaper than tier-2 even after buying the "
                "domestic hop; neighbouring-country origins can beat both."
            ),
            expected_saving_pct=_p("positioning"),
            hit_probability=0.35,
            exclusivity_group="routing",
            risks=("Separate tickets -- same buffer rules as split ticketing.",),
        ))

    if routing.is_udan_candidate(trip, route_class):
        out.append(Tactic(
            key="udan_cap",
            title="UDAN capped fare",
            detail=(
                f"Regional routes under the UDAN scheme are fare-capped at "
                f"about {UDAN_FARE_CAP_INR:,} INR for a ~1h sector. This is a "
                f"regulated ceiling, not a sale -- no timing or luck needed. "
                f"Check whether your sector or a nearby airport is on the "
                f"operational route list."
            ),
            expected_saving_pct=_p("udan_cap"),
            hit_probability=0.2,
            channel=Channel.AIRLINE_DIRECT,
        ))

    # --- Point of sale -----------------------------------------------------
    if route_class in (RouteClass.LONG_HAUL_INTL, RouteClass.SHORT_HAUL_INTL):
        card = traveller.best_forex_card
        zero_fx = bool(card and card.is_zero_forex)
        out.append(Tactic(
            key="point_of_sale",
            title="Point-of-sale arbitrage",
            detail=(
                "The same seat prices differently by the market you buy from. "
                + ("You hold a zero-forex card, so the saving passes through "
                   "intact." if zero_fx else
                   "Only worth it with a zero-forex card -- a 3.5% markup eats "
                   "most of the gain.")
            ),
            expected_saving_pct=_p("point_of_sale"),
            hit_probability=0.3 if zero_fx else 0.12,
        ))

    # --- Category fares ----------------------------------------------------
    for attr, key in (("is_armed_forces", "armed_forces"),
                      ("is_student", "student"),
                      ("is_senior", "senior")):
        if getattr(traveller, attr):
            meta = CATEGORY_FARES[key]
            out.append(Tactic(
                key=f"category_{key}",
                title=str(meta["label"]),
                detail=str(meta["note"]),
                expected_saving_pct=float(meta["typical_pct"]) / 100.0,  # type: ignore[arg-type]
                hit_probability=0.9,
                channel=Channel.AIRLINE_DIRECT,
            ))

    # --- Schedule shifting -------------------------------------------------
    if flex.date_flex_days > 0:
        out.append(Tactic(
            key="off_peak_shift",
            title="Shift to an off-peak departure",
            detail=(
                "Tuesday/Wednesday departures run materially below Friday and "
                "Sunday on the same sector, and the 5-7am and post-9pm slots "
                "add more. Note this is about the day you FLY -- the day you "
                "BUY is noise."
            ),
            expected_saving_pct=_p("off_peak_shift"),
            hit_probability=min(0.3 + 0.05 * flex.date_flex_days, 0.75),
        ))

    if flex.origin_flex and route_class is RouteClass.DOMESTIC_INDIA:
        out.append(Tactic(
            key="tier2_airport",
            title="Alternate airport",
            detail=(
                "Secondary airports carry lower charges, which passes into the "
                "fare. Weigh against ground transfer cost and time."
            ),
            expected_saving_pct=_p("tier2_airport"),
            hit_probability=0.3,
        ))

    if route_class is RouteClass.LONG_HAUL_INTL and flex.date_flex_days >= 2:
        out.append(Tactic(
            key="stopover_program",
            title="Free stopover programme",
            detail=(
                "Several hub carriers sell free or near-free multi-day "
                "stopovers. It does not cut the fare much, but it converts one "
                "trip into two destinations at near-zero marginal cost."
            ),
            expected_saving_pct=_p("stopover_program"),
            hit_probability=0.4,
        ))

    return out


def portfolio_estimate(tactics: list[Tactic], fare: float | None) -> Estimate | None:
    """Combined expected saving across tactics that can actually co-exist.

    Tactics inside an exclusivity group are mutually exclusive -- one ticket
    is bought through exactly one channel, and routed exactly one way -- so
    the group contributes only its best member. Ungrouped tactics genuinely
    stack (an open-jaw routing on an off-peak departure), so they compose
    multiplicatively on the remaining fare.

    The previous uniform 0.6 overlap factor was wrong in both directions: it
    let three mutually exclusive channel tactics all contribute, while
    discounting genuinely independent ones.
    """
    if fare is None or not tactics:
        return None

    groups: dict[str, Tactic] = {}
    independent: list[Tactic] = []
    for tactic in tactics:
        if tactic.exclusivity_group is None:
            independent.append(tactic)
            continue
        best = groups.get(tactic.exclusivity_group)
        if best is None or tactic.expected_value_pct > best.expected_value_pct:
            groups[tactic.exclusivity_group] = tactic

    remaining = 1.0
    for tactic in (*groups.values(), *independent):
        remaining *= (1.0 - tactic.expected_value_pct)

    return Estimate.around(fare * (1.0 - remaining))
