import datetime as _dt

import pytest

from traveler import (
    Cabin,
    Flexibility,
    MarketRegime,
    RouteClass,
    Traveller,
    Trip,
    Urgency,
    plan_trip,
    summarise,
)
from traveler import options, payments, routing, tactics, timing
from traveler.cli import CARD_CATALOGUE, build_parser, main

TODAY = _dt.date(2026, 8, 12)


def trip(origin="DEL", destination="BOM", days_out=40, **kw):
    return Trip(
        origin=origin,
        destination=destination,
        depart=TODAY + _dt.timedelta(days=days_out),
        **kw,
    )


# --------------------------------------------------------------------------
# Route classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("origin,dest,expected", [
    ("DEL", "BOM", RouteClass.DOMESTIC_INDIA),
    ("BLR", "DXB", RouteClass.SHORT_HAUL_INTL),
    ("BOM", "SIN", RouteClass.SHORT_HAUL_INTL),
    ("DEL", "LHR", RouteClass.LONG_HAUL_INTL),
    ("BLR", "JFK", RouteClass.LONG_HAUL_INTL),
    ("LHR", "JFK", RouteClass.FOREIGN_DOMESTIC),
])
def test_classify(origin, dest, expected):
    assert routing.classify(trip(origin, dest)) is expected


def test_unknown_foreign_airport_falls_back_to_long_haul():
    # Conservative fallback: widens the window rather than narrowing it.
    assert routing.classify(trip("DEL", "ZZZ")) is RouteClass.LONG_HAUL_INTL


def test_metro_pair_is_not_udan_candidate():
    assert not routing.is_udan_candidate(
        trip("DEL", "BOM"), RouteClass.DOMESTIC_INDIA
    )
    assert routing.is_udan_candidate(
        trip("DED", "HBX"), RouteClass.DOMESTIC_INDIA
    )


# --------------------------------------------------------------------------
# Timing curve
# --------------------------------------------------------------------------

def test_price_multiplier_bottoms_out_in_the_window():
    rc = RouteClass.DOMESTIC_INDIA
    trough = timing.price_multiplier(35, rc)
    assert trough == pytest.approx(1.0)
    assert timing.price_multiplier(3, rc) > trough
    assert timing.price_multiplier(300, rc) > trough


def test_price_multiplier_is_monotonic_through_the_cliff():
    rc = RouteClass.LONG_HAUL_INTL
    values = [timing.price_multiplier(d, rc) for d in range(0, 28)]
    assert values == sorted(values, reverse=True)


def test_rising_regime_shifts_window_earlier():
    stable = timing.window_for(RouteClass.LONG_HAUL_INTL, MarketRegime.STABLE)
    rising = timing.window_for(RouteClass.LONG_HAUL_INTL, MarketRegime.RISING)
    assert rising[0] > stable[0]


def test_urgency_inside_cliff_is_book_now():
    level, _ = timing.urgency(5, RouteClass.DOMESTIC_INDIA, MarketRegime.STABLE)
    assert level is Urgency.BOOK_NOW


def test_urgency_far_out_is_too_early():
    level, _ = timing.urgency(400, RouteClass.DOMESTIC_INDIA, MarketRegime.STABLE)
    assert level is Urgency.TOO_EARLY


def test_volatility_rises_towards_departure():
    rc = RouteClass.DOMESTIC_INDIA
    assert timing.volatility_pct(3, rc) > timing.volatility_pct(60, rc)


def test_trigger_price_loosens_as_departure_approaches():
    rc = RouteClass.DOMESTIC_INDIA
    near = timing.trigger_price(10_000, 5, rc)
    far = timing.trigger_price(10_000, 35, rc)
    assert near > far


# --------------------------------------------------------------------------
# Optionality
# --------------------------------------------------------------------------

def test_fare_hold_recommended_when_volatile_and_expensive():
    t = trip("DEL", "BOM", days_out=10, observed_fare=40_000)
    advice = options.fare_hold(t, RouteClass.DOMESTIC_INDIA, 10)
    assert advice.recommended
    assert advice.net_inr > 0


def test_fare_hold_cost_scales_with_pax():
    t = trip("DEL", "BOM", days_out=10, observed_fare=40_000, pax=4)
    advice = options.fare_hold(t, RouteClass.DOMESTIC_INDIA, 10)
    assert advice.cost_inr == pytest.approx(99 * 4)


def test_lookin_unavailable_close_to_departure():
    t = trip("DEL", "BOM", days_out=3, observed_fare=10_000)
    assert options.dgca_lookin(t, RouteClass.DOMESTIC_INDIA, 3) is None


def test_lookin_international_needs_longer_lead():
    t = trip("DEL", "LHR", days_out=10, observed_fare=90_000)
    assert options.dgca_lookin(t, RouteClass.LONG_HAUL_INTL, 10) is None
    t2 = trip("DEL", "LHR", days_out=20, observed_fare=90_000)
    assert options.dgca_lookin(t2, RouteClass.LONG_HAUL_INTL, 20) is not None


def test_coupon_versus_lookin_prefers_window_when_option_worth_more():
    t = trip("DEL", "LHR", days_out=45, observed_fare=200_000)
    lookin = options.dgca_lookin(t, RouteClass.LONG_HAUL_INTL, 45)
    verdict = options.coupon_versus_lookin(1_000, lookin)
    assert "keep the DGCA window" in verdict


def test_coupon_wins_when_option_value_is_small():
    t = trip("DEL", "BOM", days_out=30, observed_fare=6_000)
    lookin = options.dgca_lookin(t, RouteClass.DOMESTIC_INDIA, 30)
    verdict = options.coupon_versus_lookin(2_000, lookin)
    assert "Take the OTA discount" in verdict


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------

def test_cap_crushes_headline_percentage_on_expensive_fares():
    offer = {"platform": "X", "issuer": "Y", "pct": 25.0, "cap_inr": 3000,
             "days": None}
    # 25% of 200k would be 50k, but the cap delivers 3k -> 1.5%.
    assert payments.effective_pct(offer, 200_000) == pytest.approx(1.5)


def test_offer_value_respects_cap():
    offer = {"platform": "X", "issuer": "Y", "pct": 25.0, "cap_inr": 3000,
             "days": None}
    assert payments._offer_value(offer, 4_000) == pytest.approx(1_000)
    assert payments._offer_value(offer, 400_000) == pytest.approx(3_000)


def test_day_gating_filters_offers():
    monday = payments._applicable_offers(0)
    friday = payments._applicable_offers(4)
    assert len(friday) > len(monday)


def test_expensive_long_haul_prefers_direct_over_capped_coupon():
    t = trip("DEL", "LHR", days_out=60, observed_fare=250_000)
    plan = payments.optimise(
        t, Traveller(cards=(CARD_CATALOGUE["infinia"],)),
        RouteClass.LONG_HAUL_INTL, booking_date=TODAY,
        lookin_value_inr=2_500,
    )
    # Capped coupons are noise at this fare; direct keeps points + the window.
    assert plan.channel.value == "airline_direct"


def test_zero_forex_card_is_selected_for_foreign_pos():
    traveller = Traveller(cards=(CARD_CATALOGUE["infinia"],
                                 CARD_CATALOGUE["scapia"]))
    cost, note = payments.forex_cost(100_000, traveller, foreign_pos=True)
    assert cost == 0.0
    assert "Scapia" in note


def test_emi_true_cost_is_positive():
    assert payments.emi_true_cost(50_000) > 0


# --------------------------------------------------------------------------
# Tactics
# --------------------------------------------------------------------------

def test_consolidator_surfaces_for_fixed_date_premium_long_haul():
    t = trip("BOM", "JFK", days_out=45, cabin=Cabin.BUSINESS,
             flexibility=Flexibility(hard_dates=True))
    keys = {x.key for x in tactics.build(t, Traveller(), RouteClass.LONG_HAUL_INTL)}
    assert "consolidator" in keys


def test_error_fare_requires_flexibility_and_speed():
    rigid = trip("DEL", "LHR", days_out=60,
                 flexibility=Flexibility(hard_dates=True))
    keys = {x.key for x in tactics.build(rigid, Traveller(), RouteClass.LONG_HAUL_INTL)}
    assert "error_fare" not in keys

    loose = trip("DEL", "LHR", days_out=60, flexibility=Flexibility(
        date_flex_days=14, destination_flex=True, can_book_fast=True,
        origin_flex=True))
    keys = {x.key for x in tactics.build(loose, Traveller(), RouteClass.LONG_HAUL_INTL)}
    assert "error_fare" in keys


def test_category_fare_surfaces_for_armed_forces():
    t = trip("DEL", "BOM", days_out=30)
    keys = {x.key for x in tactics.build(
        t, Traveller(is_armed_forces=True), RouteClass.DOMESTIC_INDIA)}
    assert "category_armed_forces" in keys


def test_portfolio_estimate_never_exceeds_fare():
    t = trip("DEL", "LHR", days_out=60, observed_fare=100_000,
             cabin=Cabin.BUSINESS, flexibility=Flexibility(
                 date_flex_days=14, destination_flex=True,
                 accepts_self_transfer=True, can_book_fast=True,
                 origin_flex=True))
    built = tactics.build(t, Traveller(available_points=500_000),
                          RouteClass.LONG_HAUL_INTL)
    estimate = tactics.portfolio_estimate(built, 100_000)
    assert 0 < estimate < 100_000


def test_portfolio_estimate_none_without_fare():
    assert tactics.portfolio_estimate([], None) is None


# --------------------------------------------------------------------------
# Engine end-to-end
# --------------------------------------------------------------------------

def test_plan_trip_produces_complete_plan():
    t = trip("BLR", "LHR", days_out=90, cabin=Cabin.BUSINESS,
             observed_fare=185_000, baseline_fare=210_000,
             ret=TODAY + _dt.timedelta(days=110),
             flexibility=Flexibility(date_flex_days=5, origin_flex=True))
    plan = plan_trip(t, Traveller(cards=(CARD_CATALOGUE["infinia"],),
                                  available_points=400_000), today=TODAY)
    assert plan.route_class is RouteClass.LONG_HAUL_INTL
    assert plan.tactics and plan.options and plan.payment
    assert plan.rights and plan.risks
    assert plan.trigger_price_inr is not None


def test_hard_dates_flags_lost_tactics():
    t = trip("DEL", "JFK", days_out=30,
             flexibility=Flexibility(hard_dates=True))
    plan = plan_trip(t, today=TODAY)
    assert any("Hard dates" in r for r in plan.risks)


def test_us_route_surfaces_dot_rights():
    t = trip("BOM", "JFK", days_out=60)
    plan = plan_trip(t, today=TODAY)
    assert any("US DOT" in r for r in plan.rights)


def test_summarise_is_json_safe():
    import json

    t = trip("DEL", "DXB", days_out=50, observed_fare=30_000,
             baseline_fare=35_000)
    plan = plan_trip(t, today=TODAY)
    json.dumps(summarise(plan))


def test_missing_fare_is_flagged_not_fatal():
    plan = plan_trip(trip("DEL", "BOM", days_out=30), today=TODAY)
    assert any("No fare data" in r for r in plan.risks)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_runs(capsys):
    code = main([
        "--from", "BLR", "--to", "LHR",
        "--depart", "2026-12-10", "--return", "2026-12-28",
        "--cabin", "business", "--fare", "185000", "--baseline", "210000",
        "--flex-days", "5", "--origin-flex", "--cards", "infinia,scapia",
        "--today", "2026-08-12",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "TIMING" in out and "TACTICS" in out and "PAYMENT" in out


def test_cli_json_output(capsys):
    code = main([
        "--from", "DEL", "--to", "BOM", "--depart", "2026-10-01",
        "--fare", "8000", "--baseline", "7000", "--json",
        "--today", "2026-08-12",
    ])
    assert code == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["route_class"] == "domestic_india"
    assert "urgency" in payload


def test_cli_rejects_unknown_card():
    with pytest.raises(SystemExit):
        main(["--from", "DEL", "--to", "BOM", "--depart", "2026-10-01",
              "--cards", "nonexistent-card"])


def test_parser_requires_core_args():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--from", "DEL"])


# --------------------------------------------------------------------------
# Fare verdict
# --------------------------------------------------------------------------

def test_fare_below_trigger_says_buy():
    t = trip("DEL", "LHR", days_out=90, observed_fare=150_000,
             baseline_fare=210_000)
    plan = plan_trip(t, today=TODAY)
    assert plan.fare_verdict.startswith("BUY")


def test_fare_slightly_above_trigger_is_marginal():
    t = trip("DEL", "LHR", days_out=90, observed_fare=215_000,
             baseline_fare=210_000)
    plan = plan_trip(t, today=TODAY)
    assert plan.fare_verdict.startswith("MARGINAL")


def test_fare_far_above_trigger_says_hold():
    t = trip("DEL", "LHR", days_out=90, observed_fare=300_000,
             baseline_fare=210_000)
    plan = plan_trip(t, today=TODAY)
    assert plan.fare_verdict.startswith("HOLD")


def test_no_verdict_without_both_prices():
    t = trip("DEL", "LHR", days_out=90, observed_fare=150_000)
    assert plan_trip(t, today=TODAY).fare_verdict is None
