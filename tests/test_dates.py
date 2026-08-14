"""The date sweep: which day to leave, and which month to fly."""

from __future__ import annotations

import datetime as _dt

from traveler import dates
from traveler.models import RouteClass

TODAY = _dt.date(2026, 8, 14)
DIWALI_2026 = _dt.date(2026, 11, 8)      # inside Diwali, Nov 3-13, 1.40 domestic
QUIET = _dt.date(2027, 2, 10)            # no calendared peak
DOMESTIC = RouteClass.DOMESTIC_INDIA


def test_a_peak_departure_is_scored_above_a_quiet_one():
    peak = dates.nearby_dates(DIWALI_2026, DOMESTIC, TODAY, radius_days=0)[0]
    quiet = dates.nearby_dates(QUIET, DOMESTIC, TODAY, radius_days=0)[0]
    assert peak.index > quiet.index
    assert "Diwali" in peak.peaks
    assert quiet.peaks == ()


def test_nearby_sweep_finds_a_cheaper_date_outside_the_peak():
    summary = dates.summarise(DIWALI_2026, DOMESTIC, TODAY)
    better = summary["better_nearby"]
    assert better is not None
    assert "Diwali" not in better["peaks"]
    assert summary["saving_pct"] > 10


def test_a_quiet_date_is_left_alone():
    """No suggestion unless it is materially cheaper. Churn is not advice."""
    assert dates.summarise(QUIET, DOMESTIC, TODAY)["better_nearby"] is None


def test_dates_in_the_past_are_dropped_rather_than_scored_as_infinite():
    depart = TODAY + _dt.timedelta(days=3)
    days = dates.nearby_dates(depart, DOMESTIC, TODAY, radius_days=10)
    assert all(d.day >= TODAY for d in days)
    assert all(d.index != float("inf") for d in days)


def test_the_month_scan_ignores_the_booking_curve():
    """The load-bearing property.

    Month scores must answer "when should I fly", not "when should I buy".
    Since each month is scored as though booked at its own optimal moment,
    moving `today` earlier or later cannot reorder the months.
    """
    early = dates.cheapest_months(DOMESTIC, _dt.date(2026, 8, 14), months=12)
    later = dates.cheapest_months(DOMESTIC, _dt.date(2026, 9, 14), months=12)

    shared = {(m.year, m.month): m.index for m in early}
    for month in later:
        key = (month.year, month.month)
        if key in shared and month.year > 2026:  # skip partial current months
            assert month.index == shared[key]


def test_the_recommended_month_is_one_you_could_still_book_properly():
    summary = dates.summarise(DIWALI_2026, DOMESTIC, TODAY)
    best = summary["cheapest_month"]
    assert best["bookable_in_window"] is True
    assert best["calendared"] is True


def test_festival_months_score_above_quiet_months():
    by_key = {(m.year, m.month): m for m in dates.cheapest_months(DOMESTIC, TODAY, 12)}
    assert by_key[(2026, 11)].index > by_key[(2027, 2)].index   # Diwali vs quiet
    assert by_key[(2026, 12)].index > by_key[(2027, 2)].index   # Christmas vs quiet


def test_long_haul_feels_indian_festivals_less_than_domestic():
    """SEASON_SENSITIVITY scales the excess, so the same peak lands softer."""
    dom = dates.nearby_dates(DIWALI_2026, RouteClass.DOMESTIC_INDIA, TODAY, 0)[0]
    long_haul = dates.nearby_dates(DIWALI_2026, RouteClass.LONG_HAUL_INTL, TODAY, 0)[0]
    assert long_haul.index < dom.index


def test_the_summary_states_that_day_of_week_is_not_modelled():
    """Nothing in the codebase prices weekday, and the output has to say so
    rather than let the reader assume it was considered."""
    assert dates.summarise(QUIET, DOMESTIC, TODAY)["day_of_week_modelled"] is False


def test_a_scan_past_the_calendared_years_is_marked_unreliable():
    far = dates.cheapest_months(DOMESTIC, _dt.date(2028, 6, 1), months=12)
    beyond = [m for m in far if m.year > dates.LAST_CALENDARED_YEAR]
    assert beyond and all(not m.calendared for m in beyond)


def test_radius_and_month_count_are_validated():
    import pytest
    with pytest.raises(ValueError):
        dates.nearby_dates(QUIET, DOMESTIC, TODAY, radius_days=-1)
    with pytest.raises(ValueError):
        dates.cheapest_months(DOMESTIC, TODAY, months=0)
