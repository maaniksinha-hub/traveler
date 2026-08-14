"""Local web app: the engine, real flights, and a date sweep over HTTP.

Stdlib only. The core package declares no required dependencies on purpose,
and four routes plus a static directory do not justify breaking that.

    python3 -m traveler.serve

Then open http://127.0.0.1:8765. Binds to loopback only: this serves a
personal planning tool, not a public site.

Unlike ``web/traveler.html``, which runs a hand-synced JavaScript port of the
engine in the browser, this calls the real Python engine directly. There is
one implementation and nothing to keep in sync.
"""

from __future__ import annotations

import datetime as _dt
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import dates, engine, flights, tactics
from .flights import FlightLookupError
from .knowledge import SNAPSHOT_DATE
from .market import DEFAULT_MODEL
from .models import (
    Cabin,
    Card,
    Flexibility,
    MarketRegime,
    Trip,
    Traveller,
)
#: The same six real cards the CLI offers, imported rather than restated so
#: the two interfaces cannot drift apart.
from .cli import CARD_CATALOGUE

#: Static roots. Nothing outside these is ever served.
_REPO = Path(__file__).resolve().parent.parent
#: ``web/data`` is included so the page can reuse the existing 4,085-airport
#: file the Artifact build already generates, rather than shipping a second
#: copy of the same registry.
STATIC_ROOTS = (
    _REPO / "web" / "app",
    _REPO / "web" / "fonts",
    _REPO / "web" / "data",
)

#: Body cap. A trip request is a few hundred bytes; anything larger is a bug
#: or an attack, and either way should not be buffered.
MAX_BODY_BYTES = 64 * 1024

DEFAULT_PORT = 8765


# --------------------------------------------------------------------------
# Request parsing. Everything here is a trust boundary: the body is untrusted
# even from a local page, so each field is validated rather than trusted.
# --------------------------------------------------------------------------


class BadRequest(ValueError):
    """Client sent something unusable. Becomes a 400 with a readable reason."""


def _date(payload: dict, key: str, required: bool = False) -> _dt.date | None:
    raw = payload.get(key)
    if raw in (None, ""):
        if required:
            raise BadRequest(f"{key} is required")
        return None
    try:
        return _dt.date.fromisoformat(str(raw))
    except ValueError:
        raise BadRequest(f"{key} must be a date like 2026-11-20") from None


def _airport(payload: dict, key: str) -> str:
    raw = str(payload.get(key) or "").strip().upper()
    if len(raw) != 3 or not raw.isalpha():
        raise BadRequest(f"{key} must be a 3-letter airport code")
    return raw


def _number(payload: dict, key: str, low: float, high: float,
            default: float | None = None) -> float | None:
    raw = payload.get(key)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise BadRequest(f"{key} must be a number") from None
    if not low <= value <= high:
        raise BadRequest(f"{key} must be between {low:g} and {high:g}")
    return value


def _trip_from(payload: dict) -> Trip:
    try:
        cabin = Cabin(payload.get("cabin") or "economy")
    except ValueError:
        raise BadRequest("cabin is not one of the four known values") from None

    flex_days = int(_number(payload, "flex_days", 0, 14, 0) or 0)
    hard_dates = bool(payload.get("hard_dates"))
    if hard_dates and flex_days:
        # Mirrors Flexibility.__post_init__ rather than silently resolving it.
        raise BadRequest("dates cannot be both fixed and flexible")

    return Trip(
        origin=_airport(payload, "origin"),
        destination=_airport(payload, "destination"),
        depart=_date(payload, "depart", required=True),
        ret=_date(payload, "ret"),
        cabin=cabin,
        pax=int(_number(payload, "pax", 1, 9, 1) or 1),
        observed_fare=_number(payload, "fare", 0, 1e9),
        baseline_fare=_number(payload, "baseline", 0, 1e9),
        flexibility=Flexibility(
            date_flex_days=flex_days,
            destination_flex=bool(payload.get("destination_flex")),
            accepts_self_transfer=bool(payload.get("self_transfer")),
            can_book_fast=bool(payload.get("book_fast")),
            origin_flex=bool(payload.get("origin_flex")),
            hard_dates=hard_dates,
        ),
    )


def _traveller_from(payload: dict) -> Traveller:
    wanted = payload.get("cards") or []
    if not isinstance(wanted, list):
        raise BadRequest("cards must be a list")
    cards: list[Card] = [CARD_CATALOGUE[k] for k in wanted if k in CARD_CATALOGUE]

    programmes = payload.get("programmes") or []
    if isinstance(programmes, str):
        programmes = [p.strip() for p in programmes.split(",") if p.strip()]

    return Traveller(
        cards=tuple(cards),
        available_points=int(_number(payload, "points", 0, 1e9, 0) or 0),
        loyalty_programmes=tuple(str(p) for p in programmes),
        is_student=bool(payload.get("is_student")),
        is_senior=bool(payload.get("is_senior")),
        is_armed_forces=bool(payload.get("is_armed_forces")),
        indian_passport=not payload.get("non_indian_passport"),
    )


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def plan_to_dict(plan, today: _dt.date) -> dict:
    """The full plan as JSON.

    ``engine.summarise`` is deliberately not reused: it is the CLI's
    ``--json`` contract and returns only the top five tactic *keys*, with no
    detail text, so it cannot drive a UI. It also reads ``days_out`` off the
    wall clock rather than the injected ``today`` (documented in
    ``web/PORT_NOTES.md``); this takes ``today`` explicitly instead.
    """
    fare = plan.trip.reference_fare
    portfolio = tactics.portfolio_estimate(plan.tactics, fare)
    pay = plan.payment

    def est(value):
        return None if value is None else {
            "low": round(value.low, 2),
            "mid": round(value.mid, 2),
            "high": round(value.high, 2),
        }

    return {
        "route_class": plan.route_class.value,
        "urgency": plan.urgency.value,
        "timing_note": plan.timing_note,
        "days_out": plan.trip.days_out(today),
        "trigger_price_inr": plan.trigger_price_inr,
        "fare_verdict": plan.fare_verdict,
        "season_multiplier": plan.season_multiplier,
        "season_note": plan.season_note,
        "observed_fare": plan.trip.observed_fare,
        "pax": plan.trip.pax,
        "tactics": [{
            "key": t.key,
            "title": t.title,
            "detail": t.detail,
            "expected_value_pct": round(t.expected_value_pct, 4),
            "saving": est(t.saving(fare)),
            "risks": list(t.risks),
            "exclusivity_group": t.exclusivity_group,
            "channel": t.channel.value if t.channel else None,
        } for t in plan.headline_tactics],
        "portfolio_saving_inr": est(portfolio),
        "options": [{
            "instrument": o.instrument,
            "cost_inr": round(o.cost_inr, 2),
            "expected_value": est(o.expected_value),
            "recommended": o.recommended,
            "rationale": o.rationale,
        } for o in plan.options],
        "payment": None if not pay else {
            "channel": pay.channel.value,
            "steps": list(pay.steps),
            "cash_off_inr": round(pay.cash_off_inr, 2),
            "points_value_inr": round(pay.points_value_inr, 2),
            "option_value_inr": round(
                pay.option_value_inr - pay.forfeited_option_inr, 2),
            "notes": list(pay.notes),
        },
        "rights": list(plan.rights),
        "risks": list(plan.risks),
        "provenance": DEFAULT_MODEL.provenance,
        "snapshot_date": str(SNAPSHOT_DATE),
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def route_plan(payload: dict) -> dict:
    trip = _trip_from(payload)
    today = _date(payload, "today") or _dt.date.today()
    try:
        regime = MarketRegime(payload.get("regime") or "rising")
    except ValueError:
        raise BadRequest("regime must be rising, stable or falling") from None

    plan = engine.plan_trip(
        trip, _traveller_from(payload), regime=regime, today=today,
        decision_probability=_number(payload, "decision_probability", 0, 1, 0.8),
        foreign_pos=bool(payload.get("foreign_pos")),
    )
    return plan_to_dict(plan, today)


def route_flights(payload: dict) -> dict:
    """Real flights, or an honest explanation of why there are none.

    A lookup failure is a 200 with ``available: false``. The page needs to
    render its degraded state, and a transport error would push the caller
    toward retrying a scrape that is politely rate-limited.
    """
    trip = _trip_from(payload)
    try:
        found = flights.search_flights(
            trip.origin, trip.destination, trip.depart, trip.ret,
            cabin=trip.cabin.value, adults=trip.pax,
        )
    except FlightLookupError as exc:
        return {"available": False, "reason": str(exc), "flights": []}

    return {
        "available": True,
        "flights": [o.as_dict() for o in found],
        # Scraped prices are per passenger; the engine reasons in whole-party
        # money, so the caller is told which unit it just received.
        "per_passenger": True,
        "pax": trip.pax,
    }


def route_calendar(payload: dict) -> dict:
    from . import routing

    trip = _trip_from(payload)
    today = _date(payload, "today") or _dt.date.today()
    return dates.summarise(trip.depart, routing.classify(trip), today)


ROUTES = {
    "/api/plan": route_plan,
    "/api/flights": route_flights,
    "/api/calendar": route_calendar,
}


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------


def _resolve_static(url_path: str) -> Path | None:
    """Map a URL to a file, or None.

    Resolves the candidate and confirms it stays inside a known root, so a
    crafted path cannot walk out of the served directories.
    """
    relative = url_path.lstrip("/") or "index.html"
    for root in STATIC_ROOTS:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue  # escaped the root
        if candidate.is_file():
            return candidate
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "traveler"

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        # One tidy line per request. The default logs to stderr with a
        # timestamp prefix that buries the useful part.
        print(f"  {self.command} {self.path} {fmt % args}".rstrip())

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Local tool, single origin, no embedding. Cheap hardening.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = self.path.split("?", 1)[0]
        target = _resolve_static(path)
        if target is None:
            self._send_json(404, {"error": "not found"})
            return
        content_type, _ = mimetypes.guess_type(str(target))
        self._send(200, target.read_bytes(),
                   content_type or "application/octet-stream")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = self.path.split("?", 1)[0]
        handler = ROUTES.get(path)
        if handler is None:
            self._send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"error": "bad Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "request too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise BadRequest("body must be a JSON object")
            self._send_json(200, handler(payload))
        except (BadRequest, ValueError) as exc:
            # ValueError also covers the engine's own validation, such as a
            # return date before departure.
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - last-resort guard
            # A scraper or model bug must not take the whole server down mid
            # planning session.
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the traveler web app.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Loopback by default. Change only if you mean to.")
    args = parser.parse_args(argv)

    if not (STATIC_ROOTS[0] / "index.html").is_file():
        print(f"No frontend found at {STATIC_ROOTS[0]}")
        return 1

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"traveler is running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
