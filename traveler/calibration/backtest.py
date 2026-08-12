"""Replay the booking strategy over recorded history.

The question this answers is the one the engine could not previously ask:
**does the trigger-price rule actually beat just booking?**

Two baselines are non-negotiable here. A model that cannot beat "book
immediately" and "book at a fixed lead time" is not earning its complexity,
and the report says so in those words rather than burying it.

Lookahead bias is the easy way to get a flattering answer, so the baseline
fed to the trigger rule is estimated **only from departures that had already
completed** at simulated decision time. Using the departure's own realised
minimum would let the strategy see the future and would make the whole
exercise worthless.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from dataclasses import dataclass, field

from .. import timing
from ..models import RouteClass
from ..routing import classify
from ..models import Trip
from .store import FareStore

#: Departures with fewer observations than this are too sparse to simulate.
MIN_OBSERVATIONS_PER_DEPARTURE = 4

#: Prior completed departures needed before an out-of-sample baseline exists.
MIN_PRIOR_DEPARTURES = 3


@dataclass
class DepartureResult:
    """Outcome of one simulated booking decision."""

    depart_date: str
    strategy_price: float | None
    immediate_price: float
    fixed_lead_price: float | None
    realised_min: float
    triggered: bool

    def regret(self, price: float | None) -> float | None:
        """How much above the best achievable price this outcome paid."""
        if price is None:
            return None
        return price - self.realised_min


@dataclass
class BacktestReport:
    route: str
    results: list[DepartureResult] = field(default_factory=list)
    skipped: int = 0
    note: str = ""

    @property
    def n(self) -> int:
        return len(self.results)

    def _mean(self, attr: str) -> float | None:
        values = [
            getattr(r, attr) for r in self.results
            if getattr(r, attr) is not None
        ]
        return statistics.mean(values) if values else None

    @property
    def trigger_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.triggered) / len(self.results)

    def summary(self) -> str:
        if not self.results:
            return (
                f"{self.route}: not enough history to simulate. {self.note}"
            )

        strat = self._mean("strategy_price")
        immediate = self._mean("immediate_price")
        fixed = self._mean("fixed_lead_price")
        floor = self._mean("realised_min")

        lines = [
            f"{self.route}: {self.n} departures simulated "
            f"({self.skipped} skipped for sparsity)",
            f"  trigger fired on            {self.trigger_rate:>6.0%} of departures",
            f"  mean price, strategy        {strat:>12,.0f}" if strat else
            "  mean price, strategy        never fired",
            f"  mean price, book immediately{immediate:>12,.0f}",
            f"  mean price, fixed 45d lead  {fixed:>12,.0f}" if fixed else
            "  mean price, fixed 45d lead   no data",
            f"  mean realised minimum       {floor:>12,.0f}  (unreachable oracle)",
        ]

        if strat is None:
            lines.append(
                "  VERDICT: the trigger never fired. The rule is not usable on "
                "this route as configured."
            )
            return "\n".join(lines)

        beats, loses = [], []
        for label, value in (("book-immediately", immediate),
                             ("fixed-lead", fixed)):
            if value is None:
                continue
            (beats if strat < value else loses).append(
                f"{label} by {abs(value - strat):,.0f}"
            )

        # Losses are reported as prominently as wins. A strategy that beats
        # one baseline and loses to another has not been validated, and
        # printing only the win is how a model gets adopted on false
        # evidence.
        if beats:
            lines.append(f"  beats  {', '.join(beats)} INR on average")
        if loses:
            lines.append(f"  LOSES  {', '.join(loses)} INR on average")

        if loses:
            lines.append(
                "  VERDICT: NOT VALIDATED. The strategy is beaten by a naive "
                "baseline on this route, so its extra complexity is not "
                "earning anything here. Prefer the baseline that won until "
                "the fit improves."
            )
        elif beats:
            lines.append(
                "  VERDICT: strategy beats every naive baseline on this route."
            )
        else:
            lines.append("  VERDICT: inconclusive -- no baseline had data.")
        return "\n".join(lines)


def _route_class(origin: str, destination: str) -> RouteClass:
    probe = Trip(origin=origin, destination=destination, depart=_dt.date.today())
    return classify(probe)


def backtest_route(store: FareStore, origin: str, destination: str,
                   cabin: str | None = None,
                   fixed_lead_days: int = 45) -> BacktestReport:
    """Simulate the trigger rule across every tracked departure on a route."""
    report = BacktestReport(route=f"{origin.upper()}-{destination.upper()}")
    rows = store.series(origin, destination, cabin)
    if not rows:
        report.note = "No observations stored."
        return report

    by_departure: dict[str, list] = {}
    for row in rows:
        by_departure.setdefault(row["depart_date"], []).append(row)

    route_class = _route_class(origin, destination)
    completed_minima: list[float] = []

    for depart_date in sorted(by_departure):
        observations = sorted(by_departure[depart_date],
                              key=lambda r: -r["days_out"])
        if len(observations) < MIN_OBSERVATIONS_PER_DEPARTURE:
            report.skipped += 1
            continue

        realised_min = min(r["price"] for r in observations)

        # Out-of-sample baseline: only departures already completed.
        if len(completed_minima) < MIN_PRIOR_DEPARTURES:
            completed_minima.append(realised_min)
            report.skipped += 1
            continue
        baseline = statistics.median(completed_minima)

        strategy_price: float | None = None
        for row in observations:
            trigger = timing.trigger_price(
                baseline, row["days_out"], route_class
            )
            if trigger is not None and row["price"] <= trigger:
                strategy_price = row["price"]
                break

        # If the trigger never fired, the traveller flies anyway: they pay
        # the last price seen. Pretending they simply did not travel would
        # flatter the strategy enormously.
        triggered = strategy_price is not None
        if strategy_price is None:
            strategy_price = observations[-1]["price"]

        fixed_lead = min(
            (r for r in observations if r["days_out"] <= fixed_lead_days),
            key=lambda r: abs(r["days_out"] - fixed_lead_days),
            default=None,
        )

        report.results.append(DepartureResult(
            depart_date=depart_date,
            strategy_price=strategy_price,
            immediate_price=observations[0]["price"],
            fixed_lead_price=fixed_lead["price"] if fixed_lead else None,
            realised_min=realised_min,
            triggered=triggered,
        ))
        completed_minima.append(realised_min)

    if not report.results:
        report.note = (
            f"Need at least {MIN_PRIOR_DEPARTURES} completed departures for an "
            f"out-of-sample baseline plus {MIN_OBSERVATIONS_PER_DEPARTURE} "
            f"observations each. Keep the snapshot job running."
        )
    return report


def backtest_all(store: FareStore, cabin: str | None = None) -> list[BacktestReport]:
    return [
        backtest_route(store, origin, destination, cabin)
        for origin, destination in store.routes()
    ]


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .store import DEFAULT_DB

    p = argparse.ArgumentParser(
        prog="traveler.calibration.backtest",
        description="Replay the trigger rule against recorded fares.",
    )
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--route", help="ORIGIN-DEST; omit to backtest every route")
    p.add_argument("--cabin")
    args = p.parse_args(argv)

    store = FareStore(args.db)
    if args.route:
        origin, _, destination = args.route.partition("-")
        reports = [backtest_route(store, origin, destination, args.cabin)]
    else:
        reports = backtest_all(store, args.cabin)

    if not reports:
        print("No routes in the store yet. Run the snapshot job first.")
        return 0
    for report in reports:
        print(report.summary())
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
