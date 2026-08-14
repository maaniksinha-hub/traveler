"""When else to fly, and what that is worth.

Two questions that look alike and are not:

**"Can I nudge my dates?"** The traveller books now, so moving the departure
also moves ``days_out`` along the booking curve. Both effects are real, so
:func:`nearby_dates` multiplies them.

**"Which month is cheapest to fly?"** Here the booking curve is noise. It
rises again past the window (``market.price_multiplier`` climbs toward 1.20
once you are further out than ``BOOKING_WINDOWS``), so a sweep that included
it would quietly answer "book at the right time" while appearing to answer
"fly at the right time." :func:`cheapest_months` therefore holds ``days_out``
inside the optimal window and varies season alone.

Everything here is a pure function over primitives that already exist in
``seasonality``, ``market`` and ``timing``. No new pricing math.

Known gap, deliberately not papered over: **nothing in this codebase prices
day of week.** There is no day-of-week multiplier in ``knowledge.py`` or
``market.py``. The ``off_peak_shift`` tactic asserts Tuesday and Wednesday
are cheaper as static prose with a flat prior. So two dates in the same
non-peak window with the same ``days_out`` score identically here, whatever
weekday they fall on. Inventing a curve to fill that gap would mean
fabricating priors in a tool people spend money on.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as _dt
from dataclasses import dataclass

from . import seasonality, timing
from .knowledge import LAST_CALENDARED_YEAR
from .market import DEFAULT_MODEL, MarketModel
from .models import MarketRegime, RouteClass

#: How far either side of the chosen departure :func:`nearby_dates` looks.
DEFAULT_RADIUS_DAYS = 21
#: How many months ahead :func:`cheapest_months` scans.
DEFAULT_MONTHS = 12


@dataclass(frozen=True)
class DayPrice:
    """One candidate departure date, scored relative to a clean baseline."""

    day: _dt.date
    #: Relative price index. 1.0 is an unremarkable date booked in-window.
    index: float
    #: Names of any demand peaks this date falls inside, for explaining why.
    peaks: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "weekday": self.day.strftime("%a"),
            "index": round(self.index, 4),
            "peaks": list(self.peaks),
        }


@dataclass(frozen=True)
class MonthPrice:
    """One candidate month to fly, scored on season alone."""

    year: int
    month: int
    index: float
    peaks: tuple[str, ...] = ()
    #: False when this month cannot be reached at its optimal booking window
    #: from today, so the "book at the right time" premise does not hold.
    bookable_in_window: bool = True
    #: False past ``LAST_CALENDARED_YEAR``, where festival dates are unknown
    #: and the index therefore understates the real peaks.
    calendared: bool = True

    @property
    def label(self) -> str:
        return f"{_calendar.month_name[self.month]} {self.year}"

    def as_dict(self) -> dict:
        return {
            "year": self.year,
            "month": self.month,
            "label": self.label,
            "index": round(self.index, 4),
            "peaks": list(self.peaks),
            "bookable_in_window": self.bookable_in_window,
            "calendared": self.calendared,
        }


def _peak_labels(day: _dt.date) -> tuple[str, ...]:
    return tuple(label for _, label in seasonality.peaks_for(day))


def nearby_dates(depart: _dt.date,
                 route_class: RouteClass,
                 today: _dt.date | None = None,
                 radius_days: int = DEFAULT_RADIUS_DAYS,
                 regime: MarketRegime = MarketRegime.RISING,
                 model: MarketModel | None = None) -> list[DayPrice]:
    """Score each departure date within ``radius_days`` of ``depart``.

    Combines season with the booking curve, because shifting the departure
    while booking today genuinely moves both. Dates in the past are dropped
    rather than scored as infinite.
    """
    if radius_days < 0:
        raise ValueError("radius_days cannot be negative")
    today = today or _dt.date.today()
    model = model or DEFAULT_MODEL

    out: list[DayPrice] = []
    for offset in range(-radius_days, radius_days + 1):
        day = depart + _dt.timedelta(days=offset)
        days_out = (day - today).days
        if days_out < 0:
            continue
        season_mult, _ = model.season_multiplier(day, route_class)
        out.append(DayPrice(
            day=day,
            index=season_mult * model.price_multiplier(days_out, route_class),
            peaks=_peak_labels(day),
        ))
    return out


def cheapest_months(route_class: RouteClass,
                    today: _dt.date | None = None,
                    months: int = DEFAULT_MONTHS,
                    regime: MarketRegime = MarketRegime.RISING,
                    model: MarketModel | None = None) -> list[MonthPrice]:
    """Score the next ``months`` months on season alone.

    The booking curve is deliberately excluded: each month is scored as if it
    were booked at its own optimal moment, which is the only comparison that
    isolates "when to fly" from "when to buy". The index is the mean season
    multiplier across the month's flyable days.
    """
    if months < 1:
        raise ValueError("months must be at least 1")
    today = today or _dt.date.today()
    model = model or DEFAULT_MODEL
    window_open, _ = timing.window_for(route_class, regime)

    out: list[MonthPrice] = []
    year, month = today.year, today.month
    for _ in range(months):
        first = _dt.date(year, month, 1)
        last_day = _calendar.monthrange(year, month)[1]

        daily: list[float] = []
        peaks: set[str] = set()
        for dom in range(1, last_day + 1):
            day = _dt.date(year, month, dom)
            if day < today:
                continue
            season_mult, _ = model.season_multiplier(day, route_class)
            daily.append(season_mult)
            peaks.update(_peak_labels(day))

        if daily:
            # Reaching this month at its optimal window means booking at least
            # `window_open` days before its first flyable day.
            first_flyable = max(first, today)
            out.append(MonthPrice(
                year=year, month=month,
                index=sum(daily) / len(daily),
                peaks=tuple(sorted(peaks)),
                bookable_in_window=(first_flyable - today).days >= window_open,
                calendared=year <= LAST_CALENDARED_YEAR,
            ))

        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def _cheapest_month(monthly: list[MonthPrice]) -> dict | None:
    """Cheapest month a traveller could still book properly.

    Falls back to the whole list only if nothing is reachable in-window, so
    the answer is never empty, but never silently un-actionable either.
    """
    if not monthly:
        return None
    reachable = [m for m in monthly if m.bookable_in_window and m.calendared]
    return min(reachable or monthly, key=lambda m: m.index).as_dict()


def summarise(depart: _dt.date,
              route_class: RouteClass,
              today: _dt.date | None = None,
              radius_days: int = DEFAULT_RADIUS_DAYS,
              months: int = DEFAULT_MONTHS,
              regime: MarketRegime = MarketRegime.RISING,
              model: MarketModel | None = None) -> dict:
    """Both views plus the comparison the UI actually renders."""
    today = today or _dt.date.today()
    near = nearby_dates(depart, route_class, today, radius_days, regime, model)
    monthly = cheapest_months(route_class, today, months, regime, model)

    chosen = next((d for d in near if d.day == depart), None)
    best = min(near, key=lambda d: d.index) if near else None

    # Only surface an alternative that is materially cheaper. Anything under
    # 5% is inside the noise of an uncalibrated model, and telling somebody to
    # rearrange a trip for it is churn rather than advice.
    better = (
        best if chosen and best and best.day != chosen.day
        and best.index < chosen.index * 0.95
        else None
    )

    # Why it is cheaper decides what the reader should actually do. A seasonal
    # gain means the day itself is better. A gain with no seasonal component is
    # the booking curve talking: both dates sit off the optimal window, and the
    # real lever is when to buy, not when to fly. Presenting the second as
    # "pick this day" would be quietly wrong.
    reason = None
    if better and chosen:
        chosen_season, _ = (model or DEFAULT_MODEL).season_multiplier(
            chosen.day, route_class)
        better_season, _ = (model or DEFAULT_MODEL).season_multiplier(
            better.day, route_class)
        reason = "season" if better_season < chosen_season * 0.99 else "timing"

    return {
        "chosen": chosen.as_dict() if chosen else None,
        "nearby": [d.as_dict() for d in near],
        "better_nearby": better.as_dict() if better else None,
        "better_reason": reason,
        "saving_pct": (
            round((1 - better.index / chosen.index) * 100, 1)
            if better and chosen else None
        ),
        "months": [m.as_dict() for m in monthly],
        # Only months that can still be booked at their optimal window are
        # candidates. The cheapest month overall is often the current one,
        # which is cheap precisely because it is too late to reach properly.
        "cheapest_month": _cheapest_month(monthly),
        "day_of_week_modelled": False,
        "last_calendared_year": LAST_CALENDARED_YEAR,
    }
