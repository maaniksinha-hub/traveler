"""Display-flight parsing. Fixture-driven, so none of this touches a network."""

from __future__ import annotations

import datetime as _dt

import pytest

from traveler.flights import FlightOption, parse_options


class _When:
    def __init__(self, date, time):
        self.date, self.time = date, time


class _Leg:
    def __init__(self, dep, arr, duration=None):
        self.departure, self.arrival, self.duration = dep, arr, duration


class _Itinerary:
    def __init__(self, price, airlines=(), legs=()):
        self.price, self.airlines, self.flights = price, list(airlines), list(legs)


def _nonstop(price, airline="IndiGo", dep=(6, 15), arr=(8, 25)):
    day = (2026, 11, 20)
    return _Itinerary(price, [airline],
                      [_Leg(_When(day, dep), _When(day, arr), 130)])


def test_keeps_the_fields_a_person_needs_to_choose():
    """The calibration parser drops all of this; that is the whole point."""
    [opt] = parse_options([_nonstop(5480)])
    assert opt.price_inr == pytest.approx(5480.0)
    assert opt.airlines == ("IndiGo",)
    assert opt.stops == 0
    assert opt.stops_label == "Non-stop"
    assert opt.duration_label == "2h 10m"
    assert opt.as_dict()["depart_hhmm"] == "06:15"


def test_results_come_back_cheapest_first():
    got = parse_options([_nonstop(9000), _nonstop(4200), _nonstop(6100)])
    assert [o.price_inr for o in got] == [4200.0, 6100.0, 9000.0]


def test_duration_spans_layovers_rather_than_summing_legs():
    """Leg durations miss the time spent waiting between them."""
    day = (2026, 11, 20)
    two_legs = _Itinerary(4200, ["Akasa", "SpiceJet"], [
        _Leg(_When(day, (5, 0)), _When(day, (7, 0)), 120),
        _Leg(_When(day, (9, 0)), _When(day, (11, 30)), 150),
    ])
    [opt] = parse_options([two_legs])
    assert opt.duration_minutes == 390          # 05:00 to 11:30, not 120 + 150
    assert opt.stops == 1 and opt.stops_label == "1 stop"
    assert opt.airline_label == "Akasa +1"


def test_falls_back_to_leg_durations_when_clock_times_are_missing():
    broken = _Itinerary(3000, ["IndiGo"], [_Leg(_When(None, None), _When(None, None), 95)])
    [opt] = parse_options([broken])
    assert opt.duration_minutes == 95
    assert opt.as_dict()["depart_hhmm"] is None


def test_overnight_arrival_is_flagged():
    red_eye = _Itinerary(6120, ["Air India"], [
        _Leg(_When((2026, 11, 20), (22, 30)), _When((2026, 11, 21), (1, 5)), 155),
    ])
    [opt] = parse_options([red_eye])
    assert opt.as_dict()["arrives_next_day"] is True


@pytest.mark.parametrize("price", [None, "Price unavailable", 0, ""])
def test_unpriced_itineraries_are_dropped_not_rendered_as_gaps(price):
    assert parse_options([_nonstop(price)]) == []


def test_display_prices_are_coerced():
    got = parse_options([_nonstop("INR 6,120"), _nonstop("₹5,480")])
    assert [o.price_inr for o in got] == [5480.0, 6120.0]


def test_accepts_the_legacy_wrapper_shape():
    class _Wrapper:
        flights = [_nonstop(4200)]
    assert len(parse_options(_Wrapper())) == 1


def test_limit_is_applied():
    assert len(parse_options([_nonstop(p) for p in range(1000, 9000, 500)], limit=3)) == 3


def test_missing_airline_does_not_render_blank():
    [opt] = parse_options([_Itinerary(5000, [], [])])
    assert opt.airline_label == "Airline not listed"


def test_option_survives_a_result_with_no_legs_at_all():
    """Scraped input: absence of structure must not raise."""
    [opt] = parse_options([_Itinerary(5000, ["IndiGo"])])
    assert isinstance(opt, FlightOption)
    assert opt.stops == 0 and opt.duration_label is None
