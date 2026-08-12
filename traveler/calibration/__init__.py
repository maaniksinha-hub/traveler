"""Calibration: turn the hand-authored market model into a measured one.

The core engine ships with priors that are judgment, not measurement. This
package is how that changes:

    snapshot  ->  store  ->  backtest / fit  ->  FittedMarketModel

``snapshot`` accumulates observations daily (no public API hands you a back
catalogue, so this only pays off by running for months), ``backtest`` asks
whether the strategy beats naive baselines, and ``fit`` produces coefficients
the engine can load in place of the defaults.

Optional dependencies: ``pip install -e '.[calibrate]'``.
"""

from .store import FareObservation, FareStore

__all__ = ["FareObservation", "FareStore"]
