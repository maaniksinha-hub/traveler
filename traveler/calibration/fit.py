"""Fit the market model from observed fares.

Produces the coefficients ``market.FittedMarketModel`` consumes, replacing
hand-authored judgment with measurement for whichever route classes have
enough data.

Three guards decide whether a fit is honest:

**Recency and provenance weighting.** Observations are not interchangeable.
A live offer is a price someone could have transacted; Google's published
history is an aggregate; a 2022 scraped dataset describes a market that no
longer exists. Each observation is weighted by source quality decayed by age.

**Source share cap.** Weighting alone is not enough. A 300,000-row seed at
2% relative weight still carries more total mass than a few hundred live
observations, so no single source may exceed
``MAX_SOURCE_WEIGHT_SHARE`` of the total when other sources are present.
Without this, the seed silently becomes the model.

**Coverage guard.** A dataset that stops at 49 days out cannot say anything
about 200 days out. The fitted coverage window is recorded and
``FittedMarketModel`` falls back to hand-authored values outside it, rather
than extrapolating a curve into a region no data touched.

numpy and scipy are imported lazily so the core engine keeps zero
dependencies; install with ``pip install -e '.[calibrate]'``.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from collections import defaultdict
from pathlib import Path

from ..models import Trip
from ..routing import classify
from .store import CurvePoint, FareStore

#: Effective (weight-adjusted) observations needed before a fit is emitted.
MIN_EFFECTIVE_OBSERVATIONS = 200.0

#: Distinct departure dates needed -- 500 observations of one departure is
#: one data point about the booking curve, not five hundred.
MIN_DEPARTURES = 5

#: Age at which an observation counts half as much. Fares drift, markets
#: consolidate, fuel shocks happen; a year is a generous memory.
RECENCY_HALF_LIFE_DAYS = 365.0

#: No *bulk-imported* source may exceed this share of total weight when other
#: sources exist.
MAX_SOURCE_WEIGHT_SHARE = 0.5

#: Sources the share cap applies to: datasets that arrive in bulk from a
#: single static snapshot, where row count reflects how the file was scraped
#: rather than how much independent evidence it carries.
#:
#: Deliberately NOT applied to high-volume live feeds. Google's published
#: history arrives in bulk too, but it is current, route-specific and earns
#: its volume -- capping it would throw away the very data that makes
#: cold-start fast, which is exactly what an earlier version of this did.
BULK_IMPORT_SOURCES = frozenset({"kaggle-easemytrip-2022"})

#: How much to trust each source, before age is applied.
SOURCE_QUALITY: dict[str, float] = {
    "google-flights": 1.0,          # live offers, transactable
    "amadeus": 1.0,                 # live offers (Enterprise only now)
    "google-flights-history": 0.6,  # Google's aggregate, not a quotable fare
    "kaggle-easemytrip-2022": 0.4,  # static scrape, single 50-day window
}
DEFAULT_SOURCE_QUALITY = 0.5

#: Weight below which a point is ignored for coverage purposes -- otherwise
#: one ancient observation at 300 days would claim far-out coverage.
COVERAGE_WEIGHT_FLOOR = 0.05


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


def recency_weight(observed: _dt.date | None, today: _dt.date) -> float:
    """Exponential decay by age. Future dates are treated as today."""
    if observed is None:
        return 0.5
    age_days = max((today - observed).days, 0)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def point_weight(point: CurvePoint, today: _dt.date) -> float:
    quality = SOURCE_QUALITY.get(point.source, DEFAULT_SOURCE_QUALITY)
    return quality * recency_weight(point.observed_date, today)


def apply_source_cap(points: list[CurvePoint], weights: list[float],
                     max_share: float = MAX_SOURCE_WEIGHT_SHARE
                     ) -> tuple[list[float], dict[str, float]]:
    """Stop any one source from dominating purely by row count.

    Returns the adjusted weights and the resulting per-source share, which is
    reported in the fitted payload so the mix is visible.
    """
    def totals_for(current: list[float]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for point, weight in zip(points, current):
            out[point.source] += weight
        return out

    totals = totals_for(weights)
    grand = sum(totals.values())
    capped_present = any(s in BULK_IMPORT_SOURCES for s in totals)
    if (grand <= 0 or len(totals) < 2 or not 0 < max_share < 1
            or not capped_present):
        share = {k: v / grand for k, v in totals.items()} if grand > 0 else {}
        return weights, share

    # Capping a source shrinks the total too, so a naive scale of
    # max_share/share lands above the cap. Solve for the weight that makes
    # the *resulting* share equal the cap:
    #     w / (w + others) = max_share  ->  w = others * max_share/(1-max_share)
    # and iterate, since capping one source raises everyone else's share.
    ratio = max_share / (1.0 - max_share)
    scale: dict[str, float] = {source: 1.0 for source in totals}

    for _ in range(10):
        current = [w * scale[p.source] for p, w in zip(points, weights)]
        totals = totals_for(current)
        grand = sum(totals.values()) or 1.0
        over = [s for s, t in totals.items()
                if s in BULK_IMPORT_SOURCES and t / grand > max_share + 1e-9]
        if not over:
            break
        for source in over:
            others = grand - totals[source]
            if others <= 0 or totals[source] <= 0:
                continue
            scale[source] *= (others * ratio) / totals[source]

    adjusted = [w * scale[p.source] for p, w in zip(points, weights)]
    new_totals = totals_for(adjusted)
    new_grand = sum(new_totals.values()) or 1.0
    return adjusted, {k: v / new_grand for k, v in new_totals.items()}


def collect(store: FareStore, today: _dt.date | None = None,
            deconfound_season: bool = True) -> dict[str, list[CurvePoint]]:
    """Normalised curve points grouped by route class."""
    today = today or _dt.date.today()
    grouped: dict[str, list[CurvePoint]] = defaultdict(list)
    for origin, destination in store.routes():
        probe = Trip(origin=origin, destination=destination, depart=today)
        grouped[classify(probe).value].extend(
            store.normalised_curve(origin, destination, today=today,
                                   deconfound_season=deconfound_season)
        )
    return dict(grouped)


def departure_counts(store: FareStore, today: _dt.date | None = None
                     ) -> dict[str, int]:
    today = today or _dt.date.today()
    counts: dict[str, set[str]] = defaultdict(set)
    for origin, destination in store.routes():
        probe = Trip(origin=origin, destination=destination, depart=today)
        counts[classify(probe).value].update(
            store.departures(origin, destination))
    return {k: len(v) for k, v in counts.items()}


def coverage(points: list[CurvePoint], weights: list[float]) -> tuple[int, int]:
    """Days-out range actually supported by meaningfully weighted data."""
    covered = [p.days_out for p, w in zip(points, weights)
               if w >= COVERAGE_WEIGHT_FLOOR]
    if not covered:
        covered = [p.days_out for p in points]
    return (min(covered), max(covered)) if covered else (0, 0)


def fit_route_class(points: list[CurvePoint], weights: list[float]
                    ) -> dict[str, float]:
    """Weighted fit of the curve to one route class's observations."""
    np, curve_fit = _require_numeric()

    days = np.array([p.days_out for p in points], dtype=float)
    ratios = np.array([p.ratio for p in points], dtype=float)
    w = np.array(weights, dtype=float)
    w = np.clip(w, 1e-6, None)

    # curve_fit minimises ((y - f)/sigma)^2, so sigma is inverse weight.
    sigma = 1.0 / np.sqrt(w)

    popt, _ = curve_fit(
        _curve, days, ratios,
        p0=[1.0, 20.0, 0.0015, 150.0],
        sigma=sigma, absolute_sigma=False,
        bounds=([0.0, 1.0, 0.0, 30.0], [5.0, 200.0, 0.05, 400.0]),
        maxfev=20_000,
    )
    amplitude, decay, far_slope, far_start = (float(x) for x in popt)

    predicted = _curve(days, *popt)
    residual = float(np.sum(w * (ratios - predicted) ** 2))
    mean = float(np.sum(w * ratios) / np.sum(w))
    total = float(np.sum(w * (ratios - mean) ** 2))
    r_squared = 1.0 - residual / total if total > 0 else 0.0

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


def fit(store: FareStore, out_path: str | Path | None = None,
        today: _dt.date | None = None) -> dict:
    """Fit every route class with enough data; skip the rest, loudly."""
    today = today or _dt.date.today()
    deconfounded = collect(store, today, deconfound_season=True)
    raw_season = collect(store, today, deconfound_season=False)
    departures = departure_counts(store, today)

    coefficients: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    effective: dict[str, float] = {}
    mixes: dict[str, dict[str, float]] = {}
    coverages: dict[str, list[int]] = {}
    season_notes: dict[str, dict[str, object]] = {}
    skipped: dict[str, str] = {}

    for key, points in deconfounded.items():
        if not points:
            continue
        raw = [point_weight(p, today) for p in points]
        weights, mix = apply_source_cap(points, raw)
        effective_n = sum(weights)
        n_dep = departures.get(key, 0)

        if effective_n < MIN_EFFECTIVE_OBSERVATIONS:
            skipped[key] = (
                f"{effective_n:.0f} effective observations from "
                f"{len(points):,} rows, need {MIN_EFFECTIVE_OBSERVATIONS:.0f} "
                f"-- stale and low-quality sources count for less"
            )
            continue
        if n_dep < MIN_DEPARTURES:
            skipped[key] = (
                f"{n_dep} distinct departures, need {MIN_DEPARTURES} -- many "
                f"observations of one departure say little about the curve"
            )
            continue

        # Deconfounding only helps to the extent the seasonality calendar is
        # right. Dividing out an effect the data does not contain injects
        # error rather than removing it, so fit both ways and keep the better
        # one. Which wins is itself a diagnostic on the seasonality model.
        with_season = fit_route_class(points, weights)
        alternative = raw_season.get(key)
        without_season = None
        if alternative:
            alt_raw = [point_weight(p, today) for p in alternative]
            alt_weights, _ = apply_source_cap(alternative, alt_raw)
            without_season = fit_route_class(alternative, alt_weights)

        use_deconfounded = (
            without_season is None
            or with_season["r_squared"] >= without_season["r_squared"]
        )
        chosen = with_season if use_deconfounded else without_season
        chosen_points = points if use_deconfounded else alternative
        chosen_weights = weights if use_deconfounded else alt_weights

        season_notes[key] = {
            "deconfounded": use_deconfounded,
            "r_squared_deconfounded": round(with_season["r_squared"], 4),
            "r_squared_raw": (
                None if without_season is None
                else round(without_season["r_squared"], 4)
            ),
        }
        if not use_deconfounded:
            season_notes[key]["warning"] = (
                "Removing the modelled seasonality made the fit WORSE, which "
                "suggests the festival calendar in knowledge.py does not "
                "match this route class. Treat seasonal adjustments here with "
                "suspicion and re-check the calendar."
            )

        coefficients[key] = chosen
        counts[key] = len(chosen_points)
        effective[key] = round(sum(chosen_weights), 1)
        mixes[key] = {k: round(v, 3) for k, v in mix.items()}
        coverages[key] = list(coverage(chosen_points, chosen_weights))

    payload = {
        "fitted_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "coefficients": coefficients,
        "observation_counts": counts,
        "effective_observations": effective,
        "source_mix": mixes,
        "days_out_coverage": coverages,
        "seasonality": season_notes,
        "skipped": skipped,
        "min_effective_observations": MIN_EFFECTIVE_OBSERVATIONS,
        "min_departures": MIN_DEPARTURES,
        "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
        "max_source_weight_share": MAX_SOURCE_WEIGHT_SHARE,
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
            lo, hi = payload["days_out_coverage"][key]
            mix = ", ".join(
                f"{src} {share:.0%}"
                for src, share in sorted(payload["source_mix"][key].items(),
                                         key=lambda kv: -kv[1])
            )
            print(f"  {key}: n={payload['observation_counts'][key]:,} "
                  f"(effective {payload['effective_observations'][key]:,.0f}), "
                  f"R^2={coef['r_squared']:.3f}")
            print(f"    coverage {lo}-{hi} days out; mix: {mix}")
            note = payload["seasonality"].get(key, {})
            if note.get("warning"):
                print(f"    SEASONALITY WARNING: {note['warning']}")
            if hi < 120:
                print(f"    NOTE: no data beyond {hi} days out -- the engine "
                      f"falls back to hand-authored priors past there.")
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
