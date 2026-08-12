"""Fare sources.

``FareSource`` is the seam: anything that can answer "what does this
itinerary cost right now" can feed the store. ``AmadeusSource`` is the first
implementation.

Credentials come from ``AMADEUS_CLIENT_ID`` / ``AMADEUS_CLIENT_SECRET`` in the
environment. They are never written to the store, never logged, and never
included in exception messages -- error paths deliberately surface status
codes and response bodies only after scrubbing.
"""

from __future__ import annotations

import datetime as _dt
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .store import FareObservation

#: Amadeus test host. Switch to api.amadeus.com for production credentials.
TEST_HOST = "https://test.api.amadeus.com"
PRODUCTION_HOST = "https://api.amadeus.com"

CABIN_TO_AMADEUS = {
    "economy": "ECONOMY",
    "premium_economy": "PREMIUM_ECONOMY",
    "business": "BUSINESS",
    "first": "FIRST",
}


class FareSourceError(RuntimeError):
    """Raised when a source cannot answer. Never carries credentials."""


@runtime_checkable
class FareSource(Protocol):
    """Anything that can price an itinerary right now."""

    name: str

    def search(self, origin: str, destination: str, depart: _dt.date,
               ret: _dt.date | None = None, cabin: str = "economy",
               adults: int = 1, limit: int = 5) -> list[FareObservation]:
        ...


@dataclass
class AmadeusSource:
    """Amadeus Self-Service API client.

    Only the two endpoints that matter here are implemented: flight offers
    (what it costs now) and itinerary price metrics (quartiles, the fastest
    route to a defensible ``--baseline``).
    """

    client_id: str | None = field(default=None, repr=False)
    client_secret: str | None = field(default=None, repr=False)
    host: str = TEST_HOST
    timeout: int = 30
    max_retries: int = 4
    name: str = "amadeus"
    _token: str | None = field(default=None, repr=False, init=False)
    _token_expires: float = field(default=0.0, repr=False, init=False)

    def __post_init__(self) -> None:
        self.client_id = self.client_id or os.environ.get("AMADEUS_CLIENT_ID")
        self.client_secret = (
            self.client_secret or os.environ.get("AMADEUS_CLIENT_SECRET")
        )
        if not self.client_id or not self.client_secret:
            raise FareSourceError(
                "Amadeus credentials missing. Set AMADEUS_CLIENT_ID and "
                "AMADEUS_CLIENT_SECRET in the environment."
            )

    # -- plumbing ----------------------------------------------------------

    def _session(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise FareSourceError(
                "requests is required for the Amadeus source. Install the "
                "calibration extra: pip install -e '.[calibrate]'"
            ) from exc
        return requests

    def _authenticate(self) -> str:
        """Fetch and cache an OAuth2 access token."""
        if self._token and time.time() < self._token_expires - 30:
            return self._token

        requests = self._session()
        response = requests.post(
            f"{self.host}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            # Deliberately does not echo the request body.
            raise FareSourceError(
                f"Amadeus auth failed with HTTP {response.status_code}. "
                f"Check credentials and that the host ({self.host}) matches "
                f"the key type (test vs production)."
            )
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires = time.time() + float(payload.get("expires_in", 1799))
        return self._token

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET with backoff on rate limits and transient failures."""
        requests = self._session()
        last_error = ""
        for attempt in range(self.max_retries):
            token = self._authenticate()
            response = requests.get(
                f"{self.host}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code == 401:
                # Token rejected -- drop it and retry once with a fresh one.
                self._token, self._token_expires = None, 0.0
                last_error = "401 unauthorized"
            elif response.status_code == 429:
                last_error = "429 rate limited"
            elif 500 <= response.status_code < 600:
                last_error = f"{response.status_code} server error"
            else:
                raise FareSourceError(
                    f"Amadeus {path} returned HTTP {response.status_code}: "
                    f"{response.text[:400]}"
                )
            # Exponential backoff with jitter, so parallel snapshots do not
            # synchronise into a thundering herd.
            time.sleep((2 ** attempt) + random.random())
        raise FareSourceError(
            f"Amadeus {path} failed after {self.max_retries} attempts "
            f"({last_error})."
        )

    # -- public API --------------------------------------------------------

    def search(self, origin: str, destination: str, depart: _dt.date,
               ret: _dt.date | None = None, cabin: str = "economy",
               adults: int = 1, limit: int = 5) -> list[FareObservation]:
        """Current offers for an itinerary."""
        params: dict[str, Any] = {
            "originLocationCode": origin.upper(),
            "destinationLocationCode": destination.upper(),
            "departureDate": depart.isoformat(),
            "adults": adults,
            "travelClass": CABIN_TO_AMADEUS.get(cabin, "ECONOMY"),
            "currencyCode": "INR",
            "max": limit,
        }
        if ret:
            params["returnDate"] = ret.isoformat()

        payload = self._get("/v2/shopping/flight-offers", params)
        return self.parse_offers(payload, origin, destination, depart, ret, cabin)

    @staticmethod
    def parse_offers(payload: dict[str, Any], origin: str, destination: str,
                     depart: _dt.date, ret: _dt.date | None,
                     cabin: str) -> list[FareObservation]:
        """Normalise an offers response into observations.

        Kept static and pure so it can be exercised against recorded fixtures
        without network access.
        """
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        out: list[FareObservation] = []
        for offer in payload.get("data", []):
            price = offer.get("price", {})
            total = price.get("grandTotal") or price.get("total")
            if total is None:
                continue
            carriers = offer.get("validatingAirlineCodes") or []
            out.append(FareObservation(
                origin=origin.upper(),
                destination=destination.upper(),
                depart_date=depart,
                return_date=ret,
                cabin=cabin,
                price=float(total),
                currency=price.get("currency", "INR"),
                observed_at=now,
                carrier=carriers[0] if carriers else None,
                source="amadeus",
            ))
        return out

    def price_metrics(self, origin: str, destination: str,
                      depart: _dt.date, currency: str = "INR") -> dict[str, float]:
        """Quartile price metrics for a route.

        This is the fastest honest way to populate ``--baseline``: the first
        quartile is a far better "what this normally costs" than a guess.
        """
        payload = self._get(
            "/v1/analytics/itinerary-price-metrics",
            {
                "originIataCode": origin.upper(),
                "destinationIataCode": destination.upper(),
                "departureDate": depart.isoformat(),
                "currencyCode": currency,
            },
        )
        return self.parse_price_metrics(payload)

    @staticmethod
    def parse_price_metrics(payload: dict[str, Any]) -> dict[str, float]:
        """Normalise price-metric quartiles into a plain mapping."""
        data = payload.get("data") or []
        if not data:
            return {}
        metrics = data[0].get("priceMetrics", [])
        return {
            m["quartileRanking"].lower(): float(m["amount"])
            for m in metrics
            if "quartileRanking" in m and "amount" in m
        }
