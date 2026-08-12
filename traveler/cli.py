"""Command-line interface.

    python -m traveler --from BLR --to LHR --depart 2026-12-10 \
        --return 2026-12-28 --cabin business --fare 185000 --baseline 210000 \
        --flex-days 5 --origin-flex --cards infinia,scapia
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys

from . import engine
from . import tactics as tactics_mod
from .knowledge import (
    DEFAULT_POINT_REALIZATION_RATE,
    HDFC_SMARTBUY_MONTHLY_POINTS_CAP,
    SNAPSHOT_DATE,
)
from .models import (
    Cabin,
    Card,
    Flexibility,
    MarketRegime,
    Plan,
    RewardProgramme,
    Traveller,
    Trip,
)

#: Reward programmes, stated as the chain the engine actually models:
#: points earned -> monthly cap -> rupee value -> realisation haircut.
#:
#: HDFC Infinia earns ~3.3 RP per 100 INR base; SmartBuy applies a 5x
#: accelerator on flights, so 16.5 RP per 100 -- which is where the old
#: model's bogus "16.5% value back" came from. Two things cut it down:
#:
#:   point_value_inr   1.0 is the headline SmartBuy rate, achieved only on
#:                     the right redemption.
#:   realization_rate  deliberately lower than the default here, because the
#:                     Feb 2026 rules cap *redemptions* at 50,000 points
#:                     across 5 transactions per month. A large accrual
#:                     cannot all be redeemed at the good rate promptly, so
#:                     accelerated points realise worse than base points do.
_HDFC_PREMIUM = RewardProgramme(
    base_points_per_100=3.3,
    point_value_inr=1.0,
    realization_rate=0.45,
    portal_multiplier=5.0,
    monthly_points_cap=HDFC_SMARTBUY_MONTHLY_POINTS_CAP,
)

#: A few real cards, described by the properties the engine actually uses.
CARD_CATALOGUE: dict[str, Card] = {
    "infinia": Card(
        name="HDFC Infinia",
        forex_markup_pct=2.0,
        programme=_HDFC_PREMIUM,
        transfer_partners=("KrisFlyer", "Executive Club", "Miles&Smiles"),
    ),
    "diners-black": Card(
        name="HDFC Diners Club Black",
        forex_markup_pct=2.0,
        programme=_HDFC_PREMIUM,
        transfer_partners=("KrisFlyer", "Executive Club"),
    ),
    "scapia": Card(
        name="Federal Bank Scapia",
        forex_markup_pct=0.0,
        programme=RewardProgramme(base_points_per_100=2.0, point_value_inr=1.0),
    ),
    "idfc-wow": Card(
        name="IDFC FIRST WoW",
        forex_markup_pct=0.0,
        programme=RewardProgramme(base_points_per_100=1.0, point_value_inr=1.0),
    ),
    "mayura": Card(
        name="IDFC FIRST Mayura",
        forex_markup_pct=0.0,
        programme=RewardProgramme(base_points_per_100=2.5, point_value_inr=1.0),
    ),
    "magnus-burgundy": Card(
        name="Axis Magnus for Burgundy",
        forex_markup_pct=2.0,
        programme=RewardProgramme(base_points_per_100=2.4, point_value_inr=1.0),
        transfer_partners=("KrisFlyer", "Flying Blue"),
    ),
}


def _date(value: str) -> _dt.date:
    return _dt.datetime.strptime(value, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="traveler",
        description="Rank booking tactics for one trip. Decision support, not "
                    "a booking bot -- it makes no bookings.",
    )
    p.add_argument("--from", dest="origin", required=True, help="Origin IATA code")
    p.add_argument("--to", dest="destination", required=True, help="Destination IATA")
    p.add_argument("--depart", required=True, type=_date, help="YYYY-MM-DD")
    p.add_argument("--return", dest="ret", type=_date, help="YYYY-MM-DD")
    p.add_argument("--cabin", default="economy",
                   choices=[c.value for c in Cabin])
    p.add_argument("--pax", type=int, default=1)
    p.add_argument("--fare", type=float, help="Fare you have seen, INR, whole party")
    p.add_argument("--baseline", type=float, help="Normal price for this route, INR")
    p.add_argument("--multi-city", action="store_true")

    flex = p.add_argument_group("flexibility")
    flex.add_argument("--flex-days", type=int, default=0)
    flex.add_argument("--destination-flex", action="store_true")
    flex.add_argument("--self-transfer", action="store_true",
                      help="Will accept separate tickets / self-transfer")
    flex.add_argument("--book-fast", action="store_true",
                      help="Can commit within hours (needed for error fares)")
    flex.add_argument("--origin-flex", action="store_true")
    flex.add_argument("--hard-dates", action="store_true",
                      help="Dates cannot move at all")

    who = p.add_argument_group("traveller")
    who.add_argument("--cards", default="",
                     help=f"Comma-separated: {', '.join(CARD_CATALOGUE)}")
    who.add_argument("--points", type=int, default=0)
    who.add_argument("--programmes", default="",
                     help="Comma-separated loyalty programmes")
    who.add_argument("--student", action="store_true")
    who.add_argument("--senior", action="store_true")
    who.add_argument("--armed-forces", action="store_true")
    who.add_argument("--non-indian-passport", action="store_true")

    p.add_argument("--regime", default="rising",
                   choices=[r.value for r in MarketRegime],
                   help="Fare drift. Default 'rising' per the 2026 ATF shock.")
    p.add_argument("--foreign-pos", action="store_true",
                   help="Buying from a foreign point of sale")
    p.add_argument("--decision-probability", type=float, default=0.8,
                   help="Chance you actually take this trip (0-1)")
    p.add_argument("--today", type=_date, help="Override today's date")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    return p


def _traveller_from_args(args: argparse.Namespace) -> Traveller:
    cards = []
    for token in filter(None, (c.strip() for c in args.cards.split(","))):
        if token not in CARD_CATALOGUE:
            raise SystemExit(
                f"Unknown card '{token}'. Known: {', '.join(CARD_CATALOGUE)}"
            )
        cards.append(CARD_CATALOGUE[token])
    programmes = tuple(filter(None, (s.strip() for s in args.programmes.split(","))))
    return Traveller(
        cards=tuple(cards),
        loyalty_programmes=programmes,
        available_points=args.points,
        is_student=args.student,
        is_senior=args.senior,
        is_armed_forces=args.armed_forces,
        indian_passport=not args.non_indian_passport,
    )


def _rule(title: str, width: int = 72) -> str:
    return f"\n{title}\n{'-' * min(len(title), width)}"


def render(plan: Plan) -> str:
    t = plan.trip
    fare = t.reference_fare
    lines: list[str] = []

    lines.append(f"{t.origin.upper()} -> {t.destination.upper()}"
                 f"{' (return)' if t.is_round_trip else ''}"
                 f"  |  {t.cabin.value}  |  {t.pax} pax")
    lines.append(f"Departure {t.depart}  |  {t.days_out()} days out  |  "
                 f"{plan.route_class.value}")

    lines.append(_rule("TIMING"))
    lines.append(f"Verdict: {plan.urgency.value.replace('_', ' ').upper()}")
    lines.append(plan.timing_note)
    if plan.season_note:
        lines.append(f"Season: {plan.season_note}")
    if plan.trigger_price_inr is not None:
        lines.append(
            f"Trigger price: book without deliberation at or below "
            f"{plan.trigger_price_inr:,.0f} INR."
        )
    else:
        lines.append("Trigger price: supply --baseline to compute one.")
    if plan.fare_verdict:
        lines.append(f"Fare in hand: {plan.fare_verdict}")

    lines.append(_rule("TACTICS (ranked by expected value)"))
    if not plan.headline_tactics:
        lines.append("No tactics unlocked. Add flexibility or traveller detail.")
    for i, tactic in enumerate(plan.headline_tactics, 1):
        saving = tactic.saving(fare)
        money = f"  ~{saving} INR" if saving else ""
        group = f" ({tactic.exclusivity_group}-exclusive)" if tactic.exclusivity_group else ""
        lines.append(
            f"{i}. {tactic.title}{group}  "
            f"[EV {tactic.expected_value_pct * 100:.1f}%{money}]"
        )
        lines.append(f"   {tactic.detail}")
        for risk in tactic.risks:
            lines.append(f"   ! {risk}")

    portfolio = tactics_mod.portfolio_estimate(plan.tactics, fare)
    if portfolio is not None:
        lines.append(
            f"\nCombined realistic saving: {portfolio} INR. Tactics sharing an "
            f"exclusivity group count only once -- one ticket, one channel."
        )

    lines.append(_rule("OPTIONALITY"))
    for opt in plan.options:
        verdict = "BUY" if opt.recommended else "SKIP"
        lines.append(f"[{verdict}] {opt.instrument} -- cost {opt.cost_inr:,.0f} INR, "
                     f"value {opt.expected_value} INR")
        lines.append(f"   {opt.rationale}")

    if plan.payment:
        pay = plan.payment
        lines.append(_rule("PAYMENT"))
        lines.append(f"Channel: {pay.channel.value}")
        for step in pay.steps:
            lines.append(f" - {step}")
        lines.append("")
        lines.append("  Value, kept separate because the units differ:")
        lines.append(f"    cash off fare      {pay.cash_off_inr:>12,.0f} INR   (money)")
        lines.append(f"    points earned      {pay.points_value_inr:>12,.0f} INR   "
                     f"(speculative -- depends on redeeming well)")
        net_option = pay.option_value_inr - pay.forfeited_option_inr
        lines.append(f"    option value       {net_option:>12,.0f} INR   "
                     f"(not money -- the worth of keeping a choice)")
        lines.append("  These are deliberately not summed into one figure.")
        for note in pay.notes:
            lines.append(f"   . {note}")

    lines.append(_rule("YOUR RIGHTS ON THIS BOOKING"))
    for right in plan.rights:
        lines.append(f" - {right}")

    lines.append(_rule("RISK CONTROL"))
    for risk in plan.risks:
        lines.append(f" - {risk}")

    lines.append(f"\nReference data snapshot: {SNAPSHOT_DATE}. Offers, caps and "
                 f"regulations change -- re-verify before booking.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    trip = Trip(
        origin=args.origin,
        destination=args.destination,
        depart=args.depart,
        ret=args.ret,
        cabin=Cabin(args.cabin),
        pax=args.pax,
        observed_fare=args.fare,
        baseline_fare=args.baseline,
        multi_city=args.multi_city,
        flexibility=Flexibility(
            date_flex_days=args.flex_days,
            destination_flex=args.destination_flex,
            accepts_self_transfer=args.self_transfer,
            can_book_fast=args.book_fast,
            origin_flex=args.origin_flex,
            hard_dates=args.hard_dates,
        ),
    )

    plan = engine.plan_trip(
        trip,
        _traveller_from_args(args),
        regime=MarketRegime(args.regime),
        today=args.today,
        decision_probability=args.decision_probability,
        foreign_pos=args.foreign_pos,
    )

    if args.json:
        print(json.dumps(engine.summarise(plan), indent=2))
    else:
        print(render(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
