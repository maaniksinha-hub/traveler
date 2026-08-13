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

    .. deprecated::
        Amadeus decommissioned the Self-Service portal on 17 July 2026: keys
        were disabled and new registration closed. This class is retained
        because it still works against **Amadeus Enterprise** (a sales
        contract, unaffected by the shutdown) and because it is the reference
        implementation of the ``FareSource`` seam. New deployments should use
        :class:`GoogleFlightsSource`.

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


# --------------------------------------------------------------------------
# Google Flights (via SearchAPI)
# --------------------------------------------------------------------------

SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"

CABIN_TO_GOOGLE = {
    "economy": 1,
    "premium_economy": 2,
    "business": 3,
    "first": 4,
}

#: Source tags. Kept distinct because the fit weights them differently:
#: a live offer is a price you could have transacted, whereas Google's
#: history is an aggregate it publishes for the route.
SOURCE_LIVE = "google-flights"
SOURCE_HISTORY = "google-flights-history"


@dataclass
class GoogleFlightsSource:
    """Google Flights via SearchAPI.

    Replaces Amadeus Self-Service, and does something Amadeus could not: the
    ``price_insights`` block carries **``price_history``**, a series of
    ``{price, iso_date}`` points for the route. One call therefore returns
    history rather than a single present-day price, which is the difference
    between accumulating a dataset over months and fetching one now.

    ``price_insights`` is best-effort -- Google only generates it for routes
    and dates it considers popular -- so every path here degrades to
    offers-only rather than failing.
    """

    api_key: str | None = field(default=None, repr=False)
    endpoint: str = SEARCHAPI_ENDPOINT
    timeout: int = 30
    max_retries: int = 4
    currency: str = "INR"
    name: str = "google-flights"

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("SEARCHAPI_API_KEY")
        if not self.api_key:
            raise FareSourceError(
                "SearchAPI credentials missing. Set SEARCHAPI_API_KEY in the "
                "environment. Note this API is metered -- there is a free "
                "trial, not a free tier -- so budget calls with --max-calls."
            )

    # -- plumbing ----------------------------------------------------------

    def _session(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise FareSourceError(
                "requests is required. Install the calibration extra: "
                "pip install -e '.[calibrate]'"
            ) from exc
        return requests

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        """GET with backoff. The API key is passed but never surfaced."""
        requests = self._session()
        payload = {**params, "engine": "google_flights", "api_key": self.api_key}
        last_error = ""
        for attempt in range(self.max_retries):
            response = requests.get(self.endpoint, params=payload,
                                    timeout=self.timeout)
            if response.status_code == 200:
                return response.json()
            if response.status_code in (401, 403):
                raise FareSourceError(
                    f"SearchAPI rejected the request (HTTP "
                    f"{response.status_code}). Check SEARCHAPI_API_KEY and "
                    f"that the account has quota remaining."
                )
            if response.status_code == 429:
                last_error = "429 rate limited"
            elif 500 <= response.status_code < 600:
                last_error = f"{response.status_code} server error"
            else:
                raise FareSourceError(
                    f"SearchAPI returned HTTP {response.status_code}: "
                    f"{response.text[:400]}"
                )
            time.sleep((2 ** attempt) + random.random())
        raise FareSourceError(
            f"SearchAPI failed after {self.max_retries} attempts ({last_error})."
        )

    def _params(self, origin: str, destination: str, depart: _dt.date,
                ret: _dt.date | None, cabin: str, adults: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "departure_id": origin.upper(),
            "arrival_id": destination.upper(),
            "outbound_date": depart.isoformat(),
            "travel_class": CABIN_TO_GOOGLE.get(cabin, 1),
            "currency": self.currency,
            "adults": adults,
            "flight_type": "round_trip" if ret else "one_way",
        }
        if ret:
            params["return_date"] = ret.isoformat()
        return params

    # -- public API --------------------------------------------------------

    def search(self, origin: str, destination: str, depart: _dt.date,
               ret: _dt.date | None = None, cabin: str = "economy",
               adults: int = 1, limit: int = 5) -> list[FareObservation]:
        """Current offers for an itinerary."""
        payload = self._get(self._params(origin, destination, depart, ret,
                                         cabin, adults))
        return self.parse_offers(payload, origin, destination, depart, ret,
                                 cabin, limit)

    def harvest_history(self, origin: str, destination: str, depart: _dt.date,
                        ret: _dt.date | None = None,
                        cabin: str = "economy") -> list[FareObservation]:
        """Pull Google's published price history for the route.

        Returns back-dated observations -- ``observed_at`` comes from each
        point's ``iso_date``, so a single call can contribute months of curve
        data. Returns an empty list when Google published no insights, which
        is common on thin routes and is not an error.
        """
        payload = self._get(self._params(origin, destination, depart, ret,
                                         cabin, 1))
        return self.parse_history(payload, origin, destination, depart, ret,
                                  cabin)

    def price_metrics(self, origin: str, destination: str, depart: _dt.date,
                      ret: _dt.date | None = None,
                      cabin: str = "economy") -> dict[str, float]:
        """Typical price range for the route -- a sourced ``--baseline``."""
        payload = self._get(self._params(origin, destination, depart, ret,
                                         cabin, 1))
        return self.parse_price_metrics(payload)

    # -- pure parsers (fixture-testable, no network) ------------------------

    @staticmethod
    def _offer_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for key in ("best_flights", "other_flights"):
            value = payload.get(key)
            if isinstance(value, list):
                entries.extend(x for x in value if isinstance(x, dict))
        return entries

    @staticmethod
    def parse_offers(payload: dict[str, Any], origin: str, destination: str,
                     depart: _dt.date, ret: _dt.date | None, cabin: str,
                     limit: int = 5) -> list[FareObservation]:
        """Normalise offers. Unpriced entries are skipped, not fatal."""
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        out: list[FareObservation] = []
        for entry in GoogleFlightsSource._offer_entries(payload):
            price = entry.get("price")
            if price is None:
                continue
            try:
                value = float(price)
            except (TypeError, ValueError):
                continue
            legs = entry.get("flights") or []
            carrier = None
            if legs and isinstance(legs[0], dict):
                carrier = legs[0].get("airline")
            out.append(FareObservation(
                origin=origin.upper(), destination=destination.upper(),
                depart_date=depart, return_date=ret, cabin=cabin,
                price=value, currency=payload.get("search_parameters", {})
                    .get("currency", "INR"),
                observed_at=now, carrier=carrier, source=SOURCE_LIVE,
            ))
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _history_points(insights: dict[str, Any]) -> list[tuple[_dt.datetime, float]]:
        """Normalise the two shapes price_history is seen in.

        SearchAPI documents ``{price, iso_date}`` objects; sibling APIs emit
        ``[unix_timestamp, price]`` pairs. Accepting both costs a few lines
        and avoids a silent empty harvest if the shape shifts.
        """
        raw = insights.get("price_history")
        if not isinstance(raw, list):
            return []
        points: list[tuple[_dt.datetime, float]] = []
        for item in raw:
            when = price = None
            if isinstance(item, dict):
                price = item.get("price")
                stamp = item.get("iso_date") or item.get("date")
                if isinstance(stamp, str):
                    try:
                        when = _dt.datetime.fromisoformat(stamp.replace("Z", ""))
                    except ValueError:
                        when = None
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    when = _dt.datetime.utcfromtimestamp(float(item[0]))
                    price = item[1]
                except (TypeError, ValueError, OSError):
                    when = None
            if when is None or price is None:
                continue
            try:
                points.append((when, float(price)))
            except (TypeError, ValueError):
                continue
        return points

    @staticmethod
    def parse_history(payload: dict[str, Any], origin: str, destination: str,
                      depart: _dt.date, ret: _dt.date | None,
                      cabin: str) -> list[FareObservation]:
        """Expand price_insights.price_history into back-dated observations."""
        insights = payload.get("price_insights")
        if not isinstance(insights, dict):
            return []
        currency = payload.get("search_parameters", {}).get("currency", "INR")
        out: list[FareObservation] = []
        for when, price in GoogleFlightsSource._history_points(insights):
            # A history point dated after departure is nonsense; drop it
            # rather than storing a negative days_out.
            if when.date() > depart:
                continue
            out.append(FareObservation(
                origin=origin.upper(), destination=destination.upper(),
                depart_date=depart, return_date=ret, cabin=cabin,
                price=price, currency=currency, observed_at=when,
                source=SOURCE_HISTORY,
            ))
        return out

    @staticmethod
    def parse_price_metrics(payload: dict[str, Any]) -> dict[str, float]:
        """Typical price range, normalised to the same keys Amadeus uses."""
        insights = payload.get("price_insights")
        if not isinstance(insights, dict):
            return {}
        out: dict[str, float] = {}
        lowest = insights.get("lowest_price")
        if lowest is not None:
            try:
                out["minimum"] = float(lowest)
            except (TypeError, ValueError):
                pass

        band = insights.get("typical_price_range")
        low = high = None
        if isinstance(band, dict):
            low, high = band.get("low_price"), band.get("high_price")
        elif isinstance(band, (list, tuple)) and len(band) == 2:
            low, high = band
        for key, value in (("first", low), ("third", high)):
            if value is None:
                continue
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                pass

        level = insights.get("price_level")
        if isinstance(level, str):
            out["_level"] = level  # type: ignore[assignment]
        return out
