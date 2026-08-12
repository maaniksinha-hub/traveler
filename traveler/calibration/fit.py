"""Fit the market model from observed fares.

Produces the coefficients ``market.FittedMarketModel`` consumes, replacing
hand-authored judgment with measurement for whichever route classes have
enough data.

The guard that matters: below ``MIN_OBSERVATIONS`` for a route class, this
refuses to emit coefficients rather than overfitting a handful of points. A
confidently wrong fitted curve is worse than an honestly uncalibrated one.

numpy and scipy are imported lazily so the core engine keeps zero
dependencies; install the extra with ``pip install -e '.[calibrate]'``.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from pathlib import Path

from ..models import RouteClass, Trip
from ..routing import classify
from .store import FareStore

#: Below this, a route class is left uncalibrated and falls back to the
#: hand-authored curve. Deliberately conservative.
MIN_OBSERVATIONS = 200

#: Distinct departure dates needed -- 500 observations of one departure is
#: one data point about the booking curve, not five hundred.
MIN_DEPARTURES = 5


def _require_numeric():
    try:
        import numpy as np
        from scipy.optimize import curve_fit
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "Fitting needs numpy and scipy. Install the calibration extra:\n"
            "    pip install -e '.[calibrate]'"
        ) from exc
    return np, curve_fit


def _curve(days, amplitude, decay, far_slope, far_start):
    """Model form: trough of 1.0, exponential cliff, slow far-out premium."""
    import numpy as np

    near = amplitude * np.exp(-days / decay)
    far = far_slope * np.maximum(0.0, days - far_start)
    return 1.0 + near + far


def collect(store: FareStore) -> dict[str, list[tuple[int, float]]]:
    """Normalised (days_out, price ratio) points, grouped by route class."""
    grouped: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for origin, destination in store.routes():
        probe = Trip(origin=origin, destination=destination,
                     depart=_dt.date.today())
        route_class = classify(probe)
        grouped[route_class.value].extend(
            store.normalised_curve(origin, destination)
        )
    return dict(grouped)


def departure_counts(store: FareStore) -> dict[str, int]:
    counts: dict[str, set[str]] = defaultdict(set)
    for origin, destination in store.routes():
        probe = Trip(origin=origin, destination=destination,
                     depart=_dt.date.today())
        key = classify(probe).value
        counts[key].update(store.departures(origin, destination))
    return {k: len(v) for k, v in counts.items()}


def fit_route_class(points: list[tuple[int, float]]) -> dict[str, float]:
    """Fit the curve to one route class's normalised observations."""
    np, curve_fit = _require_numeric()

    days = np.array([p[0] for p in points], dtype=float)
    ratios = np.array([p[1] for p in points], dtype=float)

    popt, _ = curve_fit(
        _curve, days, ratios,
        p0=[1.0, 20.0, 0.0015, 150.0],
        bounds=([0.0, 1.0, 0.0, 30.0], [5.0, 200.0, 0.05, 400.0]),
        maxfev=20_000,
    )
    amplitude, decay, far_slope, far_start = (float(x) for x in popt)

    predicted = _curve(days, *popt)
    residual = float(np.sum((ratios - predicted) ** 2))
    total = float(np.sum((ratios - np.mean(ratios)) ** 2))
    r_squared = 1.0 - residual / total if total > 0 else 0.0

    # Volatility: dispersion of ratios in near vs far buckets.
    near_mask, far_mask = days <= 30, days > 90
    vol_near = float(np.std(ratios[near_mask]) * 100) if near_mask.any() else 12.0
    vol_far = float(np.std(ratios[far_mask]) * 100) if far_mask.any() else 2.0

    return {
        "cliff_amplitude": amplitude,
        "cliff_decay": decay,
        "far_slope": far_slope,
        "far_start": far_start,
        "vol_near": max(vol_near, 0.5),
        "vol_far": max(vol_far, 0.5),
        "vol_half_life": max(decay, 1.0),
        "r_squared": r_squared,
    }


def fit(store: FareStore, out_path: str | Path | None = None) -> dict:
    """Fit every route class with enough data; skip the rest, loudly."""
    grouped = collect(store)
    departures = departure_counts(store)

    coefficients: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    skipped: dict[str, str] = {}

    for key, points in grouped.items():
        n_obs, n_dep = len(points), departures.get(key, 0)
        if n_obs < MIN_OBSERVATIONS:
            skipped[key] = (
                f"{n_obs} observations, need {MIN_OBSERVATIONS}"
            )
            continue
        if n_dep < MIN_DEPARTURES:
            skipped[key] = (
                f"{n_dep} distinct departures, need {MIN_DEPARTURES} -- many "
                f"observations of one departure say little about the curve"
            )
            continue
        coefficients[key] = fit_route_class(points)
        counts[key] = n_obs

    payload = {
        "fitted_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "coefficients": coefficients,
        "observation_counts": counts,
        "skipped": skipped,
        "min_observations": MIN_OBSERVATIONS,
        "min_departures": MIN_DEPARTURES,
    }
    if out_path:
        Path(out_path).write_text(json.dumps(payload, indent=2))
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .store import DEFAULT_DB

    p = argparse.ArgumentParser(
        prog="traveler.calibration.fit",
        description="Fit the market model from recorded fares.",
    )
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--out", default="fitted_market.json")
    args = p.parse_args(argv)

    payload = fit(FareStore(args.db), args.out)

    if payload["coefficients"]:
        print(f"Fitted {len(payload['coefficients'])} route class(es) -> {args.out}")
        for key, coef in payload["coefficients"].items():
            print(f"  {key}: n={payload['observation_counts'][key]}, "
                  f"R^2={coef['r_squared']:.3f}")
    else:
        print("Nothing fitted -- not enough data yet.")
    for key, reason in payload["skipped"].items():
        print(f"  skipped {key}: {reason}")
    if not payload["coefficients"]:
        print("\nThe engine continues on hand-authored priors, which is the "
              "honest fallback. Keep the snapshot job running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
