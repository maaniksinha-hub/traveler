"""Descriptive market model: what fares *do*.

Strictly separated from ``timing``, which decides what you *should do*. That
boundary is what lets a model fitted from real observations replace the
hand-authored one without touching any recommendation logic.

Two implementations:

- ``DefaultMarketModel`` -- hand-authored constants. Judgment, not
  measurement. This is what ships until someone runs the calibration.
- ``FittedMarketModel`` -- coefficients produced by ``calibration.fit`` from
  observed fares.

Anything that answers "how do prices behave" belongs here. Anything that
answers "what should I do about it" does not.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import seasonality
from .knowledge import BOOKING_WINDOWS, LAST_MINUTE_CLIFF_DAYS
from .models import RouteClass


@runtime_checkable
class MarketModel(Protocol):
    """The descriptive interface the prescriptive layer depends on."""

    #: Human-readable provenance, surfaced so users know if it is calibrated.
    provenance: str

    def price_multiplier(self, days_out: int, route_class: RouteClass) -> float:
        """Expected fare as a multiple of the trough price."""
        ...

    def volatility_pct(self, days_out: int, route_class: RouteClass) -> float:
        """Expected percentage fare movement over the next few days."""
        ...

    def season_multiplier(self, depart: _dt.date,
                          route_class: RouteClass) -> tuple[float, str | None]:
        """Departure-date demand multiplier."""
        ...


@dataclass
class DefaultMarketModel:
    """Hand-authored curve. Shape is defensible; constants are judgment."""

    provenance: str = (
        "hand-authored priors (uncalibrated) -- run calibration.fit against "
        "observed fares to replace"
    )

    def price_multiplier(self, days_out: int, route_class: RouteClass) -> float:
        lo, hi = BOOKING_WINDOWS[route_class.value]
        cliff = LAST_MINUTE_CLIFF_DAYS[route_class.value]

        if days_out < 0:
            return float("inf")
        if days_out <= cliff:
            ratio = days_out / max(cliff, 1)
            return 2.0 - 0.75 * ratio            # 2.00 -> 1.25
        if days_out < lo:
            span = max(lo - cliff, 1)
            ratio = (days_out - cliff) / span
            return 1.25 - 0.25 * ratio            # 1.25 -> 1.00
        if days_out <= hi:
            return 1.0                            # the trough
        excess = days_out - hi
        return min(1.0 + 0.0015 * excess, 1.20)

    def volatility_pct(self, days_out: int, route_class: RouteClass) -> float:
        """Continuous decay from departure outward.

        Replaces an earlier step function that returned a flat 4% across the
        entire 15-90 day span and then jumped -- which made a 20-day and an
        89-day booking indistinguishable to the option model.
        """
        if days_out <= 0:
            return 0.0
        cliff = LAST_MINUTE_CLIFF_DAYS[route_class.value]
        near, far = 12.0, 2.0
        # Exponential decay with a route-scaled half-life.
        half_life = max(cliff, 1) * 1.5
        decayed = far + (near - far) * math.exp(-days_out / half_life)
        return round(decayed, 2)

    def season_multiplier(self, depart: _dt.date,
                          route_class: RouteClass) -> tuple[float, str | None]:
        return seasonality.season_multiplier(depart, route_class)


@dataclass
class FittedMarketModel:
    """Curve fitted from observed fares by ``calibration.fit``.

    Falls back to the default model for any route class the fit did not have
    enough observations for -- a partially calibrated model is useful, a
    silently extrapolated one is not.
    """

    coefficients: dict[str, dict[str, float]] = field(default_factory=dict)
    fitted_at: str = ""
    observation_counts: dict[str, int] = field(default_factory=dict)
    _fallback: DefaultMarketModel = field(default_factory=DefaultMarketModel)

    @property
    def provenance(self) -> str:
        classes = ", ".join(
            f"{k}(n={self.observation_counts.get(k, 0)})"
            for k in sorted(self.coefficients)
        ) or "nothing"
        return f"fitted {self.fitted_at} from observed fares: {classes}"

    @classmethod
    def load(cls, path: str | Path) -> "FittedMarketModel":
        payload = json.loads(Path(path).read_text())
        return cls(
            coefficients=payload.get("coefficients", {}),
            fitted_at=payload.get("fitted_at", "unknown"),
            observation_counts=payload.get("observation_counts", {}),
        )

    def _has(self, route_class: RouteClass) -> bool:
        return route_class.value in self.coefficients

    def price_multiplier(self, days_out: int, route_class: RouteClass) -> float:
        if not self._has(route_class):
            return self._fallback.price_multiplier(days_out, route_class)
        if days_out < 0:
            return float("inf")
        c = self.coefficients[route_class.value]
        # Fitted form: mult = 1 + a * exp(-days/b) + c * max(0, days - d)
        a, b = c.get("cliff_amplitude", 1.0), c.get("cliff_decay", 20.0)
        far_slope, far_start = c.get("far_slope", 0.0015), c.get("far_start", 150.0)
        near = a * math.exp(-days_out / max(b, 1e-6))
        far = far_slope * max(0.0, days_out - far_start)
        return 1.0 + near + far

    def volatility_pct(self, days_out: int, route_class: RouteClass) -> float:
        if not self._has(route_class):
            return self._fallback.volatility_pct(days_out, route_class)
        c = self.coefficients[route_class.value]
        near = c.get("vol_near", 12.0)
        far = c.get("vol_far", 2.0)
        half_life = c.get("vol_half_life", 30.0)
        if days_out <= 0:
            return 0.0
        return round(far + (near - far) * math.exp(-days_out / max(half_life, 1e-6)), 2)

    def season_multiplier(self, depart: _dt.date,
                          route_class: RouteClass) -> tuple[float, str | None]:
        # Seasonality is calendar-driven, not fitted from the booking curve.
        return self._fallback.season_multiplier(depart, route_class)


#: The model used when a caller does not supply one.
DEFAULT_MODEL: MarketModel = DefaultMarketModel()
