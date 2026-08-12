"""Engine tests.

Deliberately biased toward *invariants* over fixed values. A test that
asserts the curve bottoms out exactly where it was coded to bottom out is a
change-detector, not validation. Where a specific number is asserted it is
because that number encodes a regression we care about (S1, S2), not because
the model is known to be right.
"""

import datetime as _dt
import json

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
from traveler import market, options, payments, routing, seasonality, tactics, timing
from traveler.cli import CARD_CATALOGUE, build_parser, main
from traveler.models import Estimate, RewardProgramme

TODAY = _dt.date(2026, 8, 12)
MODEL = market.DefaultMarketModel()
ALL_CLASSES = list(RouteClass)


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


def test_udan_excludes_mainstream_trunk_routes():
    """S8 regression: DEL-JAI is a full-fare trunk route, never UDAN."""
    assert not routing.is_udan_candidate(trip("DEL", "JAI"), RouteClass.DOMESTIC_INDIA)
    assert not routing.is_udan_candidate(trip("DEL", "BOM"), RouteClass.DOMESTIC_INDIA)
    # A genuinely regional strip still flags for checking.
    assert routing.is_udan_candidate(trip("BLR", "SLV"), RouteClass.DOMESTIC_INDIA)


# --------------------------------------------------------------------------
# Market model -- invariants, not fixed values
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rc", ALL_CLASSES)
def test_multiplier_is_never_below_the_trough(rc):
    """The trough is the minimum by construction; nothing may undercut it."""
    values = [MODEL.price_multiplier(d, rc) for d in range(0, 400)]
    assert min(values) == pytest.approx(1.0)


@pytest.mark.parametrize("rc", ALL_CLASSES)
def test_multiplier_decreases_monotonically_through_the_cliff(rc):
    from traveler.knowledge import LAST_MINUTE_CLIFF_DAYS
    cliff = LAST_MINUTE_CLIFF_DAYS[rc.value]
    values = [MODEL.price_multiplier(d, rc) for d in range(0, cliff + 1)]
    assert values == sorted(values, reverse=True)


@pytest.mark.parametrize("rc", ALL_CLASSES)
def test_volatility_decreases_monotonically_with_lead_time(rc):
    """S7 regression: the old step function was flat across 15-90 days."""
    values = [MODEL.volatility_pct(d, rc) for d in range(1, 365)]
    assert values == sorted(values, reverse=True)
    assert len(set(values)) > 50, "volatility must vary, not plateau"


@pytest.mark.parametrize("rc", ALL_CLASSES)
def test_volatility_is_bounded(rc):
    for d in range(0, 500):
        assert 0.0 <= MODEL.volatility_pct(d, rc) <= 15.0


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


def test_trigger_price_loosens_as_departure_approaches():
    rc = RouteClass.DOMESTIC_INDIA
    assert timing.trigger_price(10_000, 5, rc) > timing.trigger_price(10_000, 35, rc)


def test_fitted_model_falls_back_for_uncalibrated_classes():
    fitted = market.FittedMarketModel(
        coefficients={"domestic_india": {"cliff_amplitude": 0.8, "cliff_decay": 15.0}},
        fitted_at="2026-08-12",
        observation_counts={"domestic_india": 900},
    )
    # Calibrated class uses fitted coefficients...
    assert fitted.price_multiplier(5, RouteClass.DOMESTIC_INDIA) != pytest.approx(
        MODEL.price_multiplier(5, RouteClass.DOMESTIC_INDIA)
    )
    # ...uncalibrated one silently falls back rather than extrapolating.
    assert fitted.price_multiplier(5, RouteClass.LONG_HAUL_INTL) == pytest.approx(
        MODEL.price_multiplier(5, RouteClass.LONG_HAUL_INTL)
    )
    assert "domestic_india(n=900)" in fitted.provenance


# --------------------------------------------------------------------------
# Seasonality (S3)
# --------------------------------------------------------------------------

def test_diwali_departure_costs_more_than_a_quiet_week():
    diwali = _dt.date(2026, 11, 8)
    quiet = _dt.date(2026, 9, 16)
    peak, _ = seasonality.season_multiplier(diwali, RouteClass.DOMESTIC_INDIA)
    calm, _ = seasonality.season_multiplier(quiet, RouteClass.DOMESTIC_INDIA)
    assert peak > calm == 1.0


def test_seasonality_scales_down_for_long_haul():
    diwali = _dt.date(2026, 11, 8)
    dom, _ = seasonality.season_multiplier(diwali, RouteClass.DOMESTIC_INDIA)
    lon, _ = seasonality.season_multiplier(diwali, RouteClass.LONG_HAUL_INTL)
    assert 1.0 < lon < dom


def test_new_year_window_wraps_across_the_year_boundary():
    for day in (_dt.date(2026, 12, 28), _dt.date(2027, 1, 2)):
        mult, _ = seasonality.season_multiplier(day, RouteClass.DOMESTIC_INDIA)
        assert mult > 1.0


def test_overlapping_peaks_take_max_not_product():
    """Diwali inside a holiday window is one peak, not a 1.82x monster."""
    for day in (_dt.date(2026, 11, 8), _dt.date(2026, 12, 24)):
        mult, _ = seasonality.season_multiplier(day, RouteClass.DOMESTIC_INDIA)
        assert mult <= 1.50


def test_uncalendared_year_says_so_rather_than_returning_one_silently():
    _, note = seasonality.season_multiplier(
        _dt.date(2031, 9, 15), RouteClass.DOMESTIC_INDIA
    )
    assert note and "No festival calendar" in note


def test_peak_departure_pushes_urgency_earlier():
    quiet, _ = timing.urgency(80, RouteClass.DOMESTIC_INDIA, MarketRegime.STABLE, 1.0)
    peak, _ = timing.urgency(80, RouteClass.DOMESTIC_INDIA, MarketRegime.STABLE, 1.35)
    assert quiet is Urgency.WAIT and peak is Urgency.HOLD_AND_WATCH


def test_peak_raises_the_trigger_price():
    rc = RouteClass.DOMESTIC_INDIA
    flat = timing.trigger_price(10_000, 40, rc, 1.0)
    peak = timing.trigger_price(10_000, 40, rc, 1.4)
    assert peak > flat


# --------------------------------------------------------------------------
# S1 regression -- points valuation
# --------------------------------------------------------------------------

def test_infinia_points_value_is_defensible_not_16_percent():
    """S1: the old model claimed 16.5% uncapped value-back (30,525 INR)."""
    infinia = CARD_CATALOGUE["infinia"]
    value = infinia.reward_value_inr(185_000, via_portal=True)
    assert 8_000 <= value <= 15_000, f"got {value}"
    assert value / 185_000 < 0.09


def test_monthly_cap_binds_on_large_bookings():
    infinia = CARD_CATALOGUE["infinia"]
    assert not infinia.programme.cap_binds(50_000)
    assert infinia.programme.cap_binds(1_000_000)
    # Beyond the cap, extra spend earns nothing more.
    a = infinia.reward_value_inr(1_000_000)
    b = infinia.reward_value_inr(2_000_000)
    assert a == pytest.approx(b)


def test_points_value_scales_through_every_stage():
    prog = RewardProgramme(
        base_points_per_100=10.0, point_value_inr=0.5,
        realization_rate=0.5, portal_multiplier=2.0,
    )
    # 10,000 INR -> 100 * 10 * 2 = 2,000 points -> * 0.5 * 0.5 = 500 INR
    assert prog.points_earned(10_000) == pytest.approx(2_000)
    assert prog.value_inr(10_000) == pytest.approx(500)


def test_best_reward_card_is_fare_dependent():
    """The cap means a big accelerator can lose to a smaller uncapped card."""
    capped = CARD_CATALOGUE["infinia"]
    uncapped = CARD_CATALOGUE["mayura"]
    t = Traveller(cards=(capped, uncapped))
    assert t.best_reward_card(50_000) is capped
    # At a very large fare the cap clips the accelerator.
    assert t.best_reward_card(50_000_000) is uncapped


# --------------------------------------------------------------------------
# S2 regression -- unit separation
# --------------------------------------------------------------------------

def test_payment_plan_keeps_units_separate():
    t = trip("DEL", "LHR", days_out=60, observed_fare=185_000)
    plan = payments.optimise(
        t, Traveller(cards=(CARD_CATALOGUE["infinia"],)),
        RouteClass.LONG_HAUL_INTL, booking_date=TODAY, lookin_value_inr=1_850,
    )
    assert hasattr(plan, "cash_off_inr")
    assert hasattr(plan, "points_value_inr")
    assert hasattr(plan, "option_value_inr")
    assert not hasattr(plan, "gross_discount_inr"), "S2: units must not be summed"


def test_comparable_total_is_ranking_only_and_weightable():
    from traveler.models import Channel, PaymentPlan
    p = PaymentPlan(channel=Channel.OTA, cash_off_inr=1_000,
                    points_value_inr=500, forfeited_option_inr=200)
    assert p.comparable_total(points_weight=1.0) == pytest.approx(1_300)
    # Distrust points entirely and the ranking changes -- which is the point.
    assert p.comparable_total(points_weight=0.0) == pytest.approx(800)


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------

def test_cap_crushes_headline_percentage_on_expensive_fares():
    offer = {"platform": "X", "issuer": "Y", "pct": 25.0, "cap_inr": 3000, "days": None}
    assert payments.effective_pct(offer, 200_000) == pytest.approx(1.5)


def test_offer_value_respects_cap():
    offer = {"platform": "X", "issuer": "Y", "pct": 25.0, "cap_inr": 3000, "days": None}
    assert payments._offer_value(offer, 4_000) == pytest.approx(1_000)
    assert payments._offer_value(offer, 400_000) == pytest.approx(3_000)


def test_day_gating_filters_offers():
    assert len(payments._applicable_offers(4)) > len(payments._applicable_offers(0))


def test_zero_forex_card_is_selected_for_foreign_pos():
    traveller = Traveller(cards=(CARD_CATALOGUE["infinia"], CARD_CATALOGUE["scapia"]))
    cost, note = payments.forex_cost(100_000, traveller, foreign_pos=True)
    assert cost == 0.0 and "Scapia" in note


def test_emi_true_cost_is_positive():
    assert payments.emi_true_cost(50_000) > 0


# --------------------------------------------------------------------------
# Optionality
# --------------------------------------------------------------------------

def test_fare_hold_recommended_when_volatile_and_expensive():
    t = trip("DEL", "BOM", days_out=10, observed_fare=40_000)
    advice = options.fare_hold(t, RouteClass.DOMESTIC_INDIA, 10)
    assert advice.recommended and advice.net_mid_inr > 0


def test_fare_hold_cost_scales_with_pax():
    t = trip("DEL", "BOM", days_out=10, observed_fare=40_000, pax=4)
    assert options.fare_hold(t, RouteClass.DOMESTIC_INDIA, 10).cost_inr == pytest.approx(396)


def test_lookin_unavailable_close_to_departure():
    t = trip("DEL", "BOM", days_out=3, observed_fare=10_000)
    assert options.dgca_lookin(t, RouteClass.DOMESTIC_INDIA, 3) is None


def test_lookin_international_needs_longer_lead():
    assert options.dgca_lookin(
        trip("DEL", "LHR", days_out=10, observed_fare=90_000),
        RouteClass.LONG_HAUL_INTL, 10) is None
    assert options.dgca_lookin(
        trip("DEL", "LHR", days_out=20, observed_fare=90_000),
        RouteClass.LONG_HAUL_INTL, 20) is not None


def test_coupon_versus_lookin_prefers_window_when_option_worth_more():
    t = trip("DEL", "LHR", days_out=45, observed_fare=200_000)
    lookin = options.dgca_lookin(t, RouteClass.LONG_HAUL_INTL, 45)
    assert "keep the DGCA window" in options.coupon_versus_lookin(1_000, lookin)


def test_coupon_wins_when_option_value_is_small():
    t = trip("DEL", "BOM", days_out=30, observed_fare=6_000)
    lookin = options.dgca_lookin(t, RouteClass.DOMESTIC_INDIA, 30)
    assert "Take the OTA discount" in options.coupon_versus_lookin(2_000, lookin)


# --------------------------------------------------------------------------
# Estimates
# --------------------------------------------------------------------------

def test_estimate_rejects_disordered_bounds():
    with pytest.raises(ValueError):
        Estimate(10, 5, 20)


def test_estimate_around_is_ordered_and_centred():
    e = Estimate.around(1_000)
    assert e.low <= e.mid <= e.high and e.mid == 1_000


@pytest.mark.parametrize("value", [0, 1, 500, 123_456])
def test_estimate_scaling_preserves_ordering(value):
    e = Estimate.around(value).scaled(2.5)
    assert e.low <= e.mid <= e.high


# --------------------------------------------------------------------------
# Tactics and exclusivity (S4)
# --------------------------------------------------------------------------

def test_consolidator_surfaces_for_fixed_date_premium_long_haul():
    t = trip("BOM", "JFK", days_out=45, cabin=Cabin.BUSINESS,
             flexibility=Flexibility(hard_dates=True))
    keys = {x.key for x in tactics.build(t, Traveller(), RouteClass.LONG_HAUL_INTL)}
    assert "consolidator" in keys


def test_error_fare_requires_flexibility_and_speed():
    rigid = trip("DEL", "LHR", days_out=60, flexibility=Flexibility(hard_dates=True))
    assert "error_fare" not in {
        x.key for x in tactics.build(rigid, Traveller(), RouteClass.LONG_HAUL_INTL)}

    loose = trip("DEL", "LHR", days_out=60, flexibility=Flexibility(
        date_flex_days=14, destination_flex=True, can_book_fast=True, origin_flex=True))
    assert "error_fare" in {
        x.key for x in tactics.build(loose, Traveller(), RouteClass.LONG_HAUL_INTL)}


def test_category_fare_surfaces_for_armed_forces():
    keys = {x.key for x in tactics.build(
        trip("DEL", "BOM", days_out=30), Traveller(is_armed_forces=True),
        RouteClass.DOMESTIC_INDIA)}
    assert "category_armed_forces" in keys


def test_channel_tactics_are_mutually_exclusive():
    """S4: one ticket is bought through exactly one channel."""
    t = trip("BOM", "JFK", days_out=60, cabin=Cabin.BUSINESS,
             flexibility=Flexibility(date_flex_days=14, destination_flex=True,
                                     can_book_fast=True, origin_flex=True))
    built = tactics.build(t, Traveller(available_points=500_000),
                          RouteClass.LONG_HAUL_INTL)
    channel = [x for x in built if x.exclusivity_group == "channel"]
    assert len(channel) >= 2, "need several to prove they do not all count"

    best = max(x.expected_value_pct for x in channel)
    only_channel = tactics.portfolio_estimate(channel, 100_000)
    # The group contributes its best member alone, not the compounded set.
    assert only_channel.mid == pytest.approx(100_000 * best)


def test_independent_tactics_do_compose():
    built = [x for x in tactics.build(
        trip("DEL", "LHR", days_out=60, ret=TODAY + _dt.timedelta(days=80),
             flexibility=Flexibility(date_flex_days=7)),
        Traveller(), RouteClass.LONG_HAUL_INTL) if x.exclusivity_group is None]
    assert len(built) >= 2
    combined = tactics.portfolio_estimate(built, 100_000).mid
    assert combined > max(x.expected_value_pct for x in built) * 100_000


@pytest.mark.parametrize("fare", [1_000, 50_000, 500_000])
def test_portfolio_estimate_never_exceeds_fare(fare):
    t = trip("DEL", "LHR", days_out=60, cabin=Cabin.BUSINESS,
             ret=TODAY + _dt.timedelta(days=80),
             flexibility=Flexibility(date_flex_days=14, destination_flex=True,
                                     accepts_self_transfer=True,
                                     can_book_fast=True, origin_flex=True))
    built = tactics.build(t, Traveller(available_points=500_000),
                          RouteClass.LONG_HAUL_INTL)
    est = tactics.portfolio_estimate(built, fare)
    assert 0 < est.low <= est.mid <= est.high
    assert est.high <= fare


def test_portfolio_estimate_none_without_fare():
    assert tactics.portfolio_estimate([], None) is None


# --------------------------------------------------------------------------
# Model validation
# --------------------------------------------------------------------------

def test_hard_dates_cannot_coexist_with_date_flexibility():
    """S6: the contradiction used to be accepted and silently resolved."""
    with pytest.raises(ValueError, match="contradicts"):
        Flexibility(date_flex_days=10, hard_dates=True)


def test_trip_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        Trip(origin="DEL", destination="BOM", depart=TODAY, pax=0)
    with pytest.raises(ValueError):
        Trip(origin="DEL", destination="BOM", depart=TODAY,
             ret=TODAY - _dt.timedelta(days=1))


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
    assert plan.rights and plan.risks and plan.trigger_price_inr is not None


def test_plan_discloses_model_provenance():
    plan = plan_trip(trip("DEL", "BOM", days_out=30), today=TODAY)
    assert any("provenance" in r for r in plan.risks)


def test_hard_dates_flags_lost_tactics():
    t = trip("DEL", "JFK", days_out=30, flexibility=Flexibility(hard_dates=True))
    assert any("Hard dates" in r for r in plan_trip(t, today=TODAY).risks)


@pytest.mark.parametrize("us_airport", ["JFK", "MIA", "LAX", "SEA", "ORD"])
def test_all_us_gateways_surface_dot_rights(us_airport):
    """S5: previously only 10 hardcoded airports triggered DOT rights."""
    plan = plan_trip(trip("BOM", us_airport, days_out=60), today=TODAY)
    assert any("US DOT" in r for r in plan.rights)


def test_non_us_route_does_not_claim_dot_rights():
    plan = plan_trip(trip("BOM", "LHR", days_out=60), today=TODAY)
    assert not any("US DOT" in r for r in plan.rights)


def test_summarise_is_json_safe():
    t = trip("DEL", "DXB", days_out=50, observed_fare=30_000, baseline_fare=35_000)
    json.dumps(summarise(plan_trip(t, today=TODAY)))


def test_missing_fare_is_flagged_not_fatal():
    assert any("No fare data" in r
               for r in plan_trip(trip("DEL", "BOM", days_out=30), today=TODAY).risks)


@pytest.mark.parametrize("days_out", [1, 7, 20, 45, 90, 200, 400])
@pytest.mark.parametrize("dest", ["BOM", "DXB", "LHR"])
def test_engine_never_crashes_across_the_input_space(days_out, dest):
    t = trip("DEL", dest, days_out=days_out, observed_fare=50_000,
             baseline_fare=45_000)
    plan = plan_trip(t, Traveller(cards=(CARD_CATALOGUE["infinia"],)), today=TODAY)
    assert plan.trigger_price_inr > 0
    for opt in plan.options:
        assert opt.expected_value.low <= opt.expected_value.high
        assert opt.cost_inr >= 0


# --------------------------------------------------------------------------
# Fare verdict
# --------------------------------------------------------------------------

def test_fare_below_trigger_says_buy():
    t = trip("DEL", "LHR", days_out=200, observed_fare=150_000, baseline_fare=210_000)
    assert plan_trip(t, today=TODAY).fare_verdict.startswith("BUY")


def test_fare_far_above_trigger_says_hold():
    t = trip("DEL", "LHR", days_out=200, observed_fare=400_000, baseline_fare=210_000)
    assert plan_trip(t, today=TODAY).fare_verdict.startswith("HOLD")


def test_verdict_bands_are_ordered():
    """Sweep a fare range; verdicts must go BUY -> MARGINAL -> HOLD, once."""
    seen = []
    for fare in range(150_000, 400_000, 5_000):
        t = trip("DEL", "LHR", days_out=200, observed_fare=float(fare),
                 baseline_fare=210_000)
        verdict = plan_trip(t, today=TODAY).fare_verdict.split(".")[0]
        if not seen or seen[-1] != verdict:
            seen.append(verdict)
    assert seen == ["BUY", "MARGINAL", "HOLD"]


def test_no_verdict_without_both_prices():
    assert plan_trip(trip("DEL", "LHR", days_out=90, observed_fare=150_000),
                     today=TODAY).fare_verdict is None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_runs(capsys):
    code = main([
        "--from", "BLR", "--to", "LHR", "--depart", "2026-12-10",
        "--return", "2026-12-28", "--cabin", "business", "--fare", "185000",
        "--baseline", "210000", "--flex-days", "5", "--origin-flex",
        "--cards", "infinia,scapia", "--today", "2026-08-12",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "TIMING" in out and "TACTICS" in out and "PAYMENT" in out
    # S2: the breakdown must be visible, never a single fused figure.
    assert "cash off fare" in out and "points earned" in out and "option value" in out


def test_cli_json_output(capsys):
    code = main(["--from", "DEL", "--to", "BOM", "--depart", "2026-10-01",
                 "--fare", "8000", "--baseline", "7000", "--json",
                 "--flex-days", "5", "--student", "--json",
                 "--today", "2026-08-12"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["route_class"] == "domestic_india"
    assert payload["portfolio_saving_inr"]["low"] <= payload["portfolio_saving_inr"]["high"]
    assert "payment_cash_off_inr" in payload


def test_json_portfolio_is_null_when_no_tactics_apply(capsys):
    """A rigid domestic trip with no traveller detail unlocks nothing."""
    main(["--from", "DEL", "--to", "BOM", "--depart", "2026-10-01",
          "--fare", "8000", "--baseline", "7000", "--json",
          "--today", "2026-08-12"])
    assert json.loads(capsys.readouterr().out)["portfolio_saving_inr"] is None


def test_cli_rejects_unknown_card():
    with pytest.raises(SystemExit):
        main(["--from", "DEL", "--to", "BOM", "--depart", "2026-10-01",
              "--cards", "nonexistent-card"])


def test_parser_requires_core_args():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--from", "DEL"])
