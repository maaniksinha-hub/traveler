"""Tests for the data-acquisition path.

The question this file exists to answer: can the engine get calibrated
without waiting months, and can it do so without a stale seed quietly
becoming the model?
"""

import datetime as _dt
import json
from pathlib import Path

import pytest

from traveler.calibration import fit as fitmod
from traveler.calibration import importers
from traveler.calibration.importers import (
    EASEMYTRIP_SOURCE,
    ImportError_,
    import_easemytrip_csv,
)
from traveler.calibration.snapshot import (
    WatchItem,
    expand_grid,
    run_once,
)
from traveler.calibration.sources import (
    SOURCE_HISTORY,
    SOURCE_LIVE,
    FareSourceError,
    GoogleFlightsSource,
)
from traveler.calibration.store import CurvePoint, FareObservation, FareStore
from traveler.market import FittedMarketModel
from traveler.models import RouteClass

FIXTURES = Path(__file__).parent / "fixtures"
DEPART = _dt.date(2026, 12, 10)
TODAY = _dt.date(2026, 8, 12)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def store(tmp_path):
    return FareStore(tmp_path / "fares.db")


@pytest.fixture
def gfs(monkeypatch):
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key")
    return GoogleFlightsSource()


# --------------------------------------------------------------------------
# Google Flights: offers
# --------------------------------------------------------------------------

def test_parse_offers_reads_best_and_other_flights():
    parsed = GoogleFlightsSource.parse_offers(
        fixture("searchapi_flights.json"), "DEL", "BOM", DEPART, None, "economy")
    assert [p.price for p in parsed] == [5480.0, 6120.0, 8890.0]
    assert parsed[0].carrier == "IndiGo"
    assert all(p.source == SOURCE_LIVE and p.currency == "INR" for p in parsed)


def test_parse_offers_skips_unpriced_and_unparseable_entries():
    # Fixture has one entry with no price and one with a non-numeric price.
    assert GoogleFlightsSource.parse_offers(
        fixture("searchapi_malformed_insights.json"), "DEL", "BOM", DEPART,
        None, "economy") == []


def test_parse_offers_respects_limit():
    parsed = GoogleFlightsSource.parse_offers(
        fixture("searchapi_flights.json"), "DEL", "BOM", DEPART, None,
        "economy", limit=2)
    assert len(parsed) == 2


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
    with pytest.raises(FareSourceError, match="SEARCHAPI_API_KEY"):
        GoogleFlightsSource()


def test_api_key_never_appears_in_repr(monkeypatch):
    monkeypatch.setenv("SEARCHAPI_API_KEY", "SUPERSECRETKEY")
    assert "SUPERSECRETKEY" not in repr(GoogleFlightsSource())


# --------------------------------------------------------------------------
# Google Flights: history harvesting -- the cold-start unlock
# --------------------------------------------------------------------------

def test_harvest_expands_price_history_into_back_dated_observations():
    """One call must yield a series, not a single present-day point."""
    parsed = GoogleFlightsSource.parse_history(
        fixture("searchapi_flights.json"), "DEL", "BOM", DEPART, None, "economy")
    assert len(parsed) == 5
    assert all(p.source == SOURCE_HISTORY for p in parsed)
    # observed_at comes from iso_date, so days_out spans a real range.
    days = sorted(p.days_out for p in parsed)
    assert days[0] < days[-1]
    assert parsed[0].observed_at.date() == _dt.date(2026, 5, 15)


def test_harvest_handles_timestamp_pair_format():
    """Sibling APIs emit [unix_ts, price]; accepting both avoids a silent miss."""
    parsed = GoogleFlightsSource.parse_history(
        fixture("searchapi_history_pairs.json"), "DEL", "BOM", DEPART, None,
        "economy")
    assert len(parsed) == 3
    assert all(p.price > 0 for p in parsed)


def test_absent_price_insights_degrades_to_empty_not_error():
    """Google omits insights on thin routes. That is normal, not a failure."""
    assert GoogleFlightsSource.parse_history(
        fixture("searchapi_no_insights.json"), "DEL", "BOM", DEPART, None,
        "economy") == []
    # Offers still parse fine.
    assert len(GoogleFlightsSource.parse_offers(
        fixture("searchapi_no_insights.json"), "DEL", "BOM", DEPART, None,
        "economy")) == 1


def test_malformed_history_entries_are_dropped_individually():
    parsed = GoogleFlightsSource.parse_history(
        fixture("searchapi_malformed_insights.json"), "DEL", "BOM", DEPART,
        None, "economy")
    assert len(parsed) == 1 and parsed[0].price == pytest.approx(6400.0)


def test_history_points_after_departure_are_discarded():
    payload = {
        "search_parameters": {"currency": "INR"},
        "price_insights": {"price_history": [
            {"price": 5000, "iso_date": "2026-06-01"},
            {"price": 4000, "iso_date": "2027-01-01"},
        ]},
    }
    parsed = GoogleFlightsSource.parse_history(
        payload, "DEL", "BOM", DEPART, None, "economy")
    assert len(parsed) == 1
    assert all(p.days_out >= 0 for p in parsed)


def test_price_metrics_normalise_to_the_amadeus_key_shape():
    metrics = GoogleFlightsSource.parse_price_metrics(
        fixture("searchapi_flights.json"))
    assert metrics["minimum"] == pytest.approx(5480.0)
    assert metrics["first"] == pytest.approx(5900.0)
    assert metrics["third"] == pytest.approx(9400.0)


def test_price_metrics_accept_list_form_range():
    metrics = GoogleFlightsSource.parse_price_metrics(
        fixture("searchapi_history_pairs.json"))
    assert metrics["first"] == pytest.approx(5200.0)


def test_price_metrics_empty_without_insights():
    assert GoogleFlightsSource.parse_price_metrics(
        fixture("searchapi_no_insights.json")) == {}


# --------------------------------------------------------------------------
# Grid sweep
# --------------------------------------------------------------------------

def test_grid_expands_one_itinerary_across_the_days_out_axis():
    items = expand_grid([WatchItem("DEL", "BOM", TODAY + _dt.timedelta(days=10))],
                        grid=10, stride=14, today=TODAY)
    assert len(items) == 10
    spread = [(i.depart - TODAY).days for i in items]
    assert min(spread) == 10 and max(spread) == 10 + 9 * 14


def test_grid_preserves_trip_length():
    item = WatchItem("BLR", "LHR", TODAY + _dt.timedelta(days=30),
                     ret=TODAY + _dt.timedelta(days=44))
    for shifted in expand_grid([item], grid=4, stride=20, today=TODAY):
        assert (shifted.ret - shifted.depart).days == 14


def test_grid_drops_past_departures_and_deduplicates():
    items = expand_grid([WatchItem("DEL", "BOM", TODAY - _dt.timedelta(days=30))],
                        grid=4, stride=14, today=TODAY)
    assert all(i.depart > TODAY for i in items)
    keys = {(i.origin, i.destination, i.depart) for i in items}
    assert len(keys) == len(items)


def test_grid_of_one_is_longitudinal_passthrough():
    items = [WatchItem("DEL", "BOM", DEPART)]
    assert expand_grid(items, grid=1, stride=14, today=TODAY) == items


class _FakeSource:
    name = "fake"

    def __init__(self, prices=None, history=None):
        self.prices = prices or [5000.0]
        self.history = history or []
        self.searches = 0
        self.harvests = 0

    def search(self, origin, destination, depart, ret=None, cabin="economy",
               adults=1, limit=5):
        self.searches += 1
        return [FareObservation(
            origin=origin, destination=destination, depart_date=depart,
            cabin=cabin, price=p, currency="INR",
            observed_at=_dt.datetime.combine(TODAY, _dt.time(9)),
            source=SOURCE_LIVE) for p in self.prices]

    def harvest_history(self, origin, destination, depart, ret=None,
                        cabin="economy"):
        self.harvests += 1
        return [FareObservation(
            origin=origin, destination=destination, depart_date=depart,
            cabin=cabin, price=p, currency="INR",
            observed_at=_dt.datetime.combine(d, _dt.time(9)),
            source=SOURCE_HISTORY) for d, p in self.history]


def test_call_budget_is_enforced(store):
    items = expand_grid([WatchItem("DEL", "BOM", TODAY + _dt.timedelta(days=10))],
                        grid=20, stride=7, today=TODAY)
    source = _FakeSource()
    result = run_once(items, source, store, verbose=False, max_calls=5,
                      today=TODAY)
    assert result.calls == 5 and result.budget_exhausted
    assert source.searches == 5, "must stop calling a metered API at the cap"


def test_history_harvest_contributes_extra_observations(store):
    history = [(TODAY - _dt.timedelta(days=n), 6000.0 + n) for n in (90, 60, 30)]
    source = _FakeSource(history=history)
    result = run_once([WatchItem("DEL", "BOM", TODAY + _dt.timedelta(days=40))],
                      source, store, verbose=False, harvest_history=True,
                      today=TODAY)
    assert source.harvests == 1
    assert result.recorded == 4, "1 live offer + 3 historical points"


def test_history_harvest_respects_the_budget(store):
    source = _FakeSource(history=[(TODAY, 5000.0)])
    result = run_once([WatchItem("DEL", "BOM", TODAY + _dt.timedelta(days=40))],
                      source, store, verbose=False, harvest_history=True,
                      max_calls=1, today=TODAY)
    assert source.harvests == 0 and result.calls == 1


# --------------------------------------------------------------------------
# Importers
# --------------------------------------------------------------------------

def test_easemytrip_import_round_trips(store):
    report = import_easemytrip_csv(FIXTURES / "easemytrip_sample.csv", store)
    assert report.rows_read == 9
    # Rows dropped: same-city, unknown city, negative days_left, bad price.
    assert report.rows_skipped == 4
    assert report.rows_imported == 5
    assert store.count() == 5
    assert report.max_days_out == 49


def test_import_preserves_days_out_exactly(store):
    import_easemytrip_csv(FIXTURES / "easemytrip_sample.csv", store)
    days = sorted(r["days_out"] for r in store.series("DEL", "BOM"))
    assert days == [1, 1, 7]


def test_import_tags_provenance(store):
    import_easemytrip_csv(FIXTURES / "easemytrip_sample.csv", store)
    assert all(r["source"] == EASEMYTRIP_SOURCE for r in store.series("DEL", "BOM"))


def test_import_maps_cities_and_cabins(store):
    import_easemytrip_csv(FIXTURES / "easemytrip_sample.csv", store)
    business = [r for r in store.series("BLR", "CCU") if r["cabin"] == "business"]
    assert len(business) == 1 and business[0]["price"] == pytest.approx(44450)


def test_wrong_schema_is_rejected_not_silently_imported(store):
    with pytest.raises(ImportError_, match="missing required column"):
        import_easemytrip_csv(FIXTURES / "wrong_schema.csv", store)
    assert store.count() == 0


def test_missing_file_explains_where_to_get_it(store):
    with pytest.raises(ImportError_, match="kaggle"):
        import_easemytrip_csv("/nonexistent/path.csv", store)


def test_generic_importer_requires_a_complete_column_map(store):
    with pytest.raises(ImportError_, match="column_map is missing"):
        importers.import_csv(FIXTURES / "easemytrip_sample.csv", store,
                             column_map={"origin": "source_city"},
                             source="custom")


# --------------------------------------------------------------------------
# Normalisation: the grid-sweep trap
# --------------------------------------------------------------------------

def _obs(days_out, price, depart, source=SOURCE_LIVE, origin="DEL",
         destination="BOM"):
    return FareObservation(
        origin=origin, destination=destination, depart_date=depart,
        cabin="economy", price=price, currency="INR",
        observed_at=_dt.datetime.combine(
            depart - _dt.timedelta(days=days_out), _dt.time(9)),
        source=source)


def test_cross_sectional_sweep_does_not_collapse_to_all_ones(store):
    """The trap: one observation per departure means its own min IS itself.

    Normalising each departure by its own minimum would make every ratio
    exactly 1.0 and teach the fit nothing from a grid sweep.
    """
    base = TODAY + _dt.timedelta(days=10)
    # A completed departure with coverage, to establish a reference...
    done = TODAY - _dt.timedelta(days=5)
    store.record([_obs(d, p, done) for d, p in
                  ((90, 9000), (60, 7000), (30, 6000), (7, 12000))])
    # ...then a cross-sectional sweep: one point per future departure.
    store.record([_obs(10 + i * 14, 6000 + i * 400,
                       base + _dt.timedelta(days=i * 14)) for i in range(6)])

    curve = store.normalised_curve("DEL", "BOM", today=TODAY,
                                   deconfound_season=False)
    swept = [p for p in curve if p.basis == "route_reference"]
    assert len(swept) == 6
    assert len({round(p.ratio, 4) for p in swept}) > 1, \
        "swept points must vary, not all normalise to 1.0"


def test_route_reference_used_when_no_departure_qualifies(store):
    base = TODAY + _dt.timedelta(days=20)
    store.record([_obs(20 + i * 10, 5000 + i * 500,
                       base + _dt.timedelta(days=i * 10)) for i in range(4)])
    curve = store.normalised_curve("DEL", "BOM", today=TODAY,
                                   deconfound_season=False)
    assert curve and all(p.basis == "route_reference" for p in curve)
    assert min(p.ratio for p in curve) == pytest.approx(1.0)


def test_seasonality_is_divided_out(store):
    """Two identical lead times, one in a peak: deconfounding converges them."""
    quiet = _dt.date(2026, 9, 16)
    diwali = _dt.date(2026, 11, 8)
    store.record([_obs(40, 6000, quiet), _obs(40, 8400, diwali)])

    raw = store.normalised_curve("DEL", "BOM", today=TODAY,
                                 deconfound_season=False)
    fixed = store.normalised_curve("DEL", "BOM", today=TODAY,
                                   deconfound_season=True)
    spread_raw = max(p.ratio for p in raw) - min(p.ratio for p in raw)
    spread_fixed = max(p.ratio for p in fixed) - min(p.ratio for p in fixed)
    assert spread_fixed < spread_raw, "peak premium should shrink once divided out"


def test_synthetic_dates_are_exempt_from_seasonality(store):
    """Imported departure dates are fabricated; their calendar means nothing."""
    diwali = _dt.date(2026, 11, 8)
    store.record([_obs(40, 8400, diwali, source=EASEMYTRIP_SOURCE)])
    with_season = store.normalised_curve("DEL", "BOM", today=TODAY,
                                         deconfound_season=True)
    without = store.normalised_curve("DEL", "BOM", today=TODAY,
                                     deconfound_season=False)
    assert [p.ratio for p in with_season] == [p.ratio for p in without]


# --------------------------------------------------------------------------
# Weighting -- the failure mode this whole design exists to prevent
# --------------------------------------------------------------------------

def test_recency_weight_decays_with_age():
    assert fitmod.recency_weight(TODAY, TODAY) == pytest.approx(1.0)
    year_old = TODAY - _dt.timedelta(days=365)
    assert fitmod.recency_weight(year_old, TODAY) == pytest.approx(0.5)
    assert fitmod.recency_weight(TODAY - _dt.timedelta(days=1600), TODAY) < 0.06


def test_live_data_outweighs_stale_seed_per_observation():
    fresh = CurvePoint(30, 1.1, _dt.datetime.combine(TODAY, _dt.time()).isoformat(),
                       SOURCE_LIVE)
    stale = CurvePoint(30, 1.1, "2022-03-06T12:00:00", EASEMYTRIP_SOURCE)
    assert fitmod.point_weight(fresh, TODAY) > 20 * fitmod.point_weight(stale, TODAY)


def test_source_cap_stops_a_large_seed_dominating_by_row_count():
    """300k stale rows at 2% weight still outweigh 500 live ones without a cap."""
    points = ([CurvePoint(30, 1.1, "2022-03-06T12:00:00", EASEMYTRIP_SOURCE)] * 30_000
              + [CurvePoint(30, 1.1,
                            _dt.datetime.combine(TODAY, _dt.time()).isoformat(),
                            SOURCE_LIVE)] * 300)
    raw = [fitmod.point_weight(p, TODAY) for p in points]

    seed_share_uncapped = (
        sum(w for p, w in zip(points, raw) if p.source == EASEMYTRIP_SOURCE)
        / sum(raw)
    )
    assert seed_share_uncapped > 0.5, "precondition: seed would dominate"

    _, mix = fitmod.apply_source_cap(points, raw)
    assert mix[EASEMYTRIP_SOURCE] <= fitmod.MAX_SOURCE_WEIGHT_SHARE + 1e-6


def test_source_cap_is_a_noop_with_a_single_source():
    points = [CurvePoint(30, 1.1, "2026-08-01T00:00:00", SOURCE_LIVE)] * 10
    raw = [1.0] * 10
    adjusted, mix = fitmod.apply_source_cap(points, raw)
    assert adjusted == raw and mix == {SOURCE_LIVE: 1.0}


def test_coverage_ignores_negligibly_weighted_points():
    points = [CurvePoint(30, 1.0, _dt.datetime.combine(TODAY, _dt.time()).isoformat(),
                         SOURCE_LIVE),
              CurvePoint(300, 1.2, "2015-01-01T00:00:00", EASEMYTRIP_SOURCE)]
    weights = [fitmod.point_weight(p, TODAY) for p in points]
    lo, hi = fitmod.coverage(points, weights)
    assert hi == 30, "an ancient far-out point must not claim coverage"


def test_fit_refuses_on_thin_effective_data(store):
    import_easemytrip_csv(FIXTURES / "easemytrip_sample.csv", store)
    payload = fitmod.fit(store, today=TODAY)
    assert payload["coefficients"] == {}
    assert any("effective observations" in reason
               for reason in payload["skipped"].values())


def test_fitted_payload_reports_the_mix_and_coverage(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "fitted_at": "2026-08-12",
        "coefficients": {"domestic_india": {"cliff_amplitude": 0.9,
                                            "cliff_decay": 18.0}},
        "observation_counts": {"domestic_india": 5_000},
        "source_mix": {"domestic_india": {EASEMYTRIP_SOURCE: 0.5,
                                          SOURCE_LIVE: 0.5}},
        "days_out_coverage": {"domestic_india": [1, 49]},
    }))
    model = FittedMarketModel.load(path)
    assert "1-49d" in model.provenance
    assert model.is_seed_dominated


# --------------------------------------------------------------------------
# Coverage guard -- seed data must not produce far-out coefficients
# --------------------------------------------------------------------------

def _seeded_model():
    return FittedMarketModel(
        coefficients={"domestic_india": {"cliff_amplitude": 0.9,
                                         "cliff_decay": 18.0,
                                         "far_slope": 0.002,
                                         "far_start": 100.0}},
        fitted_at="2026-08-12",
        observation_counts={"domestic_india": 5_000},
        days_out_coverage={"domestic_india": [1, 49]},
    )


def test_fitted_curve_used_inside_coverage():
    model = _seeded_model()
    assert model.covers(30, RouteClass.DOMESTIC_INDIA)
    from traveler.market import DefaultMarketModel
    assert model.price_multiplier(30, RouteClass.DOMESTIC_INDIA) != pytest.approx(
        DefaultMarketModel().price_multiplier(30, RouteClass.DOMESTIC_INDIA))


def test_beyond_coverage_falls_back_rather_than_extrapolating():
    """A dataset stopping at 49 days cannot speak about 200 days out."""
    from traveler.market import DefaultMarketModel

    model = _seeded_model()
    assert not model.covers(200, RouteClass.DOMESTIC_INDIA)
    assert model.price_multiplier(200, RouteClass.DOMESTIC_INDIA) == pytest.approx(
        DefaultMarketModel().price_multiplier(200, RouteClass.DOMESTIC_INDIA))
    assert model.volatility_pct(200, RouteClass.DOMESTIC_INDIA) == pytest.approx(
        DefaultMarketModel().volatility_pct(200, RouteClass.DOMESTIC_INDIA))


def test_uncalibrated_route_class_still_falls_back():
    from traveler.market import DefaultMarketModel

    model = _seeded_model()
    assert model.price_multiplier(30, RouteClass.LONG_HAUL_INTL) == pytest.approx(
        DefaultMarketModel().price_multiplier(30, RouteClass.LONG_HAUL_INTL))


@pytest.mark.live
def test_searchapi_live_smoke():  # pragma: no cover - requires credentials
    """Run manually: pytest -m live with SEARCHAPI_API_KEY set."""
    source = GoogleFlightsSource()
    depart = _dt.date.today() + _dt.timedelta(days=45)
    offers = source.search("DEL", "BOM", depart)
    assert offers and all(o.price > 0 for o in offers)


# --------------------------------------------------------------------------
# Seasonality self-correction
# --------------------------------------------------------------------------

def _synthetic_store(tmp_path, seasonal_truth: bool):
    """Data generated from a known curve, optionally with a real seasonal term."""
    import math

    from traveler import seasonality

    rng = __import__("random").Random(3)
    store = FareStore(tmp_path / f"s{seasonal_truth}.db")
    for i in range(60):
        depart = TODAY + _dt.timedelta(days=7 + i * 5)
        season = 1.0
        if seasonal_truth:
            season, _ = seasonality.season_multiplier(
                depart, RouteClass.DOMESTIC_INDIA)
        leads = [(depart - TODAY).days] + [
            (depart - (TODAY - _dt.timedelta(days=b * 12))).days
            for b in range(1, 9)
        ]
        for days_out in leads:
            if days_out <= 0:
                continue
            price = 6000 * (1 + math.exp(-days_out / 18.0)) * season \
                * rng.uniform(0.97, 1.03)
            store.record([FareObservation(
                "DEL", "BOM", depart, "economy", round(price, 2), "INR",
                _dt.datetime.combine(depart - _dt.timedelta(days=days_out),
                                     _dt.time(9)),
                source=SOURCE_LIVE)])
    return store


def test_fit_recovers_a_known_curve_from_one_grid_sweep(tmp_path):
    """The cold-start claim, checked against ground truth."""
    store = _synthetic_store(tmp_path, seasonal_truth=True)
    payload = fitmod.fit(store, today=TODAY)
    coef = payload["coefficients"]["domestic_india"]
    assert coef["r_squared"] > 0.8
    assert 12.0 < coef["cliff_decay"] < 30.0, "true decay is 18"
    assert 0.7 < coef["cliff_amplitude"] < 1.3, "true amplitude is 1.0"


def test_fit_deconfounds_when_the_data_really_is_seasonal(tmp_path):
    store = _synthetic_store(tmp_path, seasonal_truth=True)
    note = fitmod.fit(store, today=TODAY)["seasonality"]["domestic_india"]
    assert note["deconfounded"] is True
    assert note["r_squared_deconfounded"] > note["r_squared_raw"]
    assert "warning" not in note


def test_fit_declines_to_deconfound_when_it_would_hurt(tmp_path):
    """Dividing out an effect the data lacks injects error, not removes it."""
    store = _synthetic_store(tmp_path, seasonal_truth=False)
    payload = fitmod.fit(store, today=TODAY)
    note = payload["seasonality"]["domestic_india"]
    assert note["deconfounded"] is False
    assert note["r_squared_raw"] > note["r_squared_deconfounded"]
    assert "warning" in note and "festival calendar" in note["warning"]
    # And the kept fit is the good one, not the damaged one.
    assert payload["coefficients"]["domestic_india"]["r_squared"] > 0.8


# --------------------------------------------------------------------------
# Free sources: airport registry (OurAirports) and fast-flights
# --------------------------------------------------------------------------

def test_registry_covers_far_more_than_the_hand_tables():
    from traveler import airports
    from traveler.knowledge import AIRPORT_COUNTRY, INDIAN_AIRPORTS

    stats = airports.coverage()
    assert stats["airports"] > 3_000
    assert stats["indian"] > len(INDIAN_AIRPORTS)
    assert stats["countries"] > 100
    assert stats["airports"] > len(AIRPORT_COUNTRY)


@pytest.mark.parametrize("code,country", [
    ("DEL", "IN"), ("BOM", "IN"), ("MIA", "US"), ("LAX", "US"),
    ("BCN", "ES"), ("HAN", "VN"), ("DOH", "QA"), ("NBO", "KE"),
])
def test_registry_resolves_countries_the_hand_table_missed(code, country):
    from traveler import airports
    assert airports.country_of(code) == country


def test_registry_falls_back_to_hand_tables_when_data_missing(monkeypatch):
    """A missing data file degrades the engine, it must not break it."""
    from traveler import airports

    monkeypatch.setattr(airports, "DATA_FILE", Path("/nonexistent/airports.csv"))
    airports._registry.cache_clear()
    try:
        assert airports.country_of("DEL") == "IN"      # from INDIAN_AIRPORTS
        assert airports.country_of("JFK") == "US"      # from AIRPORT_COUNTRY
        assert airports.coverage()["airports"] == 0
    finally:
        airports._registry.cache_clear()


def test_unknown_code_still_returns_none():
    from traveler import airports
    assert airports.country_of("ZZZ") is None


def test_registry_improves_route_classification():
    from traveler.models import Trip
    from traveler.routing import classify

    # Neither endpoint was in the old hand table.
    assert classify(Trip(origin="BCN", destination="HAN",
                         depart=DEPART)) is RouteClass.FOREIGN_DOMESTIC
    assert classify(Trip(origin="DEL", destination="HAN",
                         depart=DEPART)) is RouteClass.LONG_HAUL_INTL


def test_us_gateways_resolve_from_the_registry():
    from traveler import airports
    for code in ("JFK", "MIA", "LAX", "SEA", "AUS", "PDX", "BNA", "SJC"):
        assert airports.country_of(code) == "US", code


# --- fast-flights (free, keyless) ------------------------------------------

class _FakeFlight:
    def __init__(self, price, airlines=None):
        self.price = price
        self.airlines = airlines or []


def test_fastflights_parses_a_resultlist():
    from traveler.calibration.sources import SOURCE_FASTFLIGHTS, FastFlightsSource

    result = [_FakeFlight(5480, ["IndiGo"]), _FakeFlight("INR 6,120", ["Air India"])]
    parsed = FastFlightsSource.parse_result(
        result, "DEL", "BOM", DEPART, None, "economy")
    assert [p.price for p in parsed] == [5480.0, 6120.0]
    assert parsed[0].carrier == "IndiGo"
    assert all(p.source == SOURCE_FASTFLIGHTS for p in parsed)


def test_fastflights_accepts_the_legacy_wrapper_shape():
    from traveler.calibration.sources import FastFlightsSource

    class _Wrapper:
        flights = [_FakeFlight(4200, ["Akasa"])]

    parsed = FastFlightsSource.parse_result(
        _Wrapper(), "DEL", "BOM", DEPART, None, "economy")
    assert len(parsed) == 1 and parsed[0].price == pytest.approx(4200.0)


def test_fastflights_skips_unpriced_itineraries():
    from traveler.calibration.sources import FastFlightsSource

    result = [_FakeFlight(None), _FakeFlight("Price unavailable"),
              _FakeFlight(0), _FakeFlight(7300, ["SpiceJet"])]
    parsed = FastFlightsSource.parse_result(
        result, "DEL", "BOM", DEPART, None, "economy")
    assert len(parsed) == 1 and parsed[0].price == pytest.approx(7300.0)


@pytest.mark.parametrize("raw,expected", [
    ("INR 5,480", 5480.0),
    ("₹12,300", 12300.0),
    ("Rs. 8999", 8999.0),          # the full stop must not become a decimal
    ("Rs. 8,999.50", 8999.5),
    ("$1,234.56", 1234.56),
    (4500, 4500.0),
    ("0", None),
    ("", None),
    ("Price unavailable", None),
    (None, None),
])
def test_price_coercion_handles_display_strings(raw, expected):
    from traveler.calibration.sources import _coerce_price

    got = _coerce_price(raw)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_fastflights_reports_a_clear_error_when_not_installed(monkeypatch):
    from traveler.calibration.sources import FastFlightsSource

    source = FastFlightsSource()
    monkeypatch.setattr(
        source, "_api",
        lambda: (_ for _ in ()).throw(FareSourceError("pip install fast-flights")))
    with pytest.raises(FareSourceError, match="pip install fast-flights"):
        source.search("DEL", "BOM", DEPART)


def test_fastflights_satisfies_the_faresource_protocol():
    from traveler.calibration.sources import FareSource, FastFlightsSource
    assert isinstance(FastFlightsSource(), FareSource)


def test_fastflights_needs_no_credentials(monkeypatch):
    """The whole point: no key, no metering, no signup."""
    from traveler.calibration.sources import FastFlightsSource

    for var in ("SEARCHAPI_API_KEY", "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert FastFlightsSource().name == "fast-flights"


def test_fastflights_import_dependencies_are_all_declared():
    """Regression: fast-flights 3.0.2 imports typing_extensions without
    declaring it, so `pip install fast-flights` alone fails at import on a
    clean environment. Our [free] extra pins it explicitly; this test fails
    if that pin is ever dropped while the upstream gap persists.
    """
    pytest.importorskip("fast_flights")
    import importlib.metadata as md

    declared = " ".join(md.requires("fast-flights") or []).lower()
    if "typing" in declared:
        return  # upstream fixed it; the pin can go

    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    assert "typing_extensions" in pyproject, (
        "fast-flights still does not declare typing_extensions, so the "
        "[free] extra must keep pinning it"
    )
