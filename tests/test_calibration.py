"""Calibration tests.

Network is never touched. The Amadeus client's parsing is pure and static
precisely so it can be exercised against recorded fixtures; the transport
layer is tested with a stub session. Live tests are marked ``live`` and
skipped by default -- run them yourself with real credentials.
"""

import datetime as _dt
import json
import random
from pathlib import Path

import pytest

from traveler.calibration import backtest as bt
from traveler.calibration.sources import (
    TEST_HOST,
    AmadeusSource,
    FareSource,
    FareSourceError,
)
from traveler.calibration.snapshot import WatchItem, load_watchlist, run_once
from traveler.calibration.store import FareObservation, FareStore

FIXTURES = Path(__file__).parent / "fixtures"
DEPART = _dt.date(2026, 12, 10)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def store(tmp_path):
    return FareStore(tmp_path / "fares.db")


def obs(days_out: int, price: float, depart=DEPART, **kw) -> FareObservation:
    return FareObservation(
        origin=kw.pop("origin", "DEL"),
        destination=kw.pop("destination", "BOM"),
        depart_date=depart,
        cabin="economy",
        price=price,
        currency="INR",
        observed_at=_dt.datetime.combine(
            depart - _dt.timedelta(days=days_out), _dt.time(9, 0)
        ),
        source="test",
        **kw,
    )


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

def test_store_roundtrip(store):
    assert store.record([obs(60, 5000), obs(30, 6200)]) == 2
    assert store.count() == 2
    assert store.routes() == [("DEL", "BOM")]


def test_duplicate_observations_are_idempotent(store):
    batch = [obs(60, 5000)]
    assert store.record(batch) == 1
    assert store.record(batch) == 0, "re-running a snapshot must be harmless"
    assert store.count() == 1


def test_empty_batch_is_a_noop(store):
    assert store.record([]) == 0


def test_days_out_is_derived_not_stored_by_caller():
    assert obs(45, 5000).days_out == 45


def test_normalised_curve_divides_by_each_departures_own_minimum(store):
    store.record([obs(60, 10_000), obs(30, 5_000), obs(7, 15_000)])
    curve = dict(store.normalised_curve("DEL", "BOM"))
    assert curve[30] == pytest.approx(1.0)
    assert curve[60] == pytest.approx(2.0)
    assert curve[7] == pytest.approx(3.0)


def test_series_filters_by_cabin(store):
    store.record([obs(60, 5000)])
    assert store.series("DEL", "BOM", cabin="economy")
    assert not store.series("DEL", "BOM", cabin="business")


def test_failed_write_does_not_corrupt_store(store, monkeypatch):
    store.record([obs(60, 5000)])
    bad = obs(30, 6000)
    monkeypatch.setattr(type(bad), "as_row", lambda self: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ValueError):
        store.record([bad])
    assert store.count() == 1


# --------------------------------------------------------------------------
# Amadeus parsing (fixtures, no network)
# --------------------------------------------------------------------------

def test_parse_offers_normalises_every_priced_result():
    parsed = AmadeusSource.parse_offers(
        fixture("amadeus_offers.json"), "DEL", "LHR", DEPART, None, "economy")
    assert len(parsed) == 3
    assert parsed[0].price == pytest.approx(48_250.0)
    assert parsed[0].carrier == "AI"
    assert all(o.source == "amadeus" and o.currency == "INR" for o in parsed)


def test_parse_offers_prefers_grand_total_over_total():
    """grandTotal includes fees; total does not. Always take the payable one."""
    parsed = AmadeusSource.parse_offers(
        fixture("amadeus_offers.json"), "DEL", "LHR", DEPART, None, "economy")
    assert parsed[1].price == pytest.approx(52_400.0)


def test_parse_offers_handles_empty_result():
    assert AmadeusSource.parse_offers(
        fixture("amadeus_offers_empty.json"), "DEL", "LHR", DEPART, None,
        "economy") == []


def test_parse_offers_skips_unpriced_entries_without_crashing():
    parsed = AmadeusSource.parse_offers(
        fixture("amadeus_offers_malformed.json"), "DEL", "BOM", DEPART, None,
        "economy")
    assert len(parsed) == 1
    assert parsed[0].carrier == "6E"


def test_parse_price_metrics_yields_quartiles():
    metrics = AmadeusSource.parse_price_metrics(fixture("amadeus_price_metrics.json"))
    assert metrics["minimum"] == pytest.approx(4_200.0)
    assert metrics["first"] == pytest.approx(5_800.0)
    assert metrics["medium"] < metrics["third"] < metrics["maximum"]


def test_parse_price_metrics_handles_empty_payload():
    assert AmadeusSource.parse_price_metrics({"data": []}) == {}


def test_amadeus_requires_credentials(monkeypatch):
    monkeypatch.delenv("AMADEUS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AMADEUS_CLIENT_SECRET", raising=False)
    with pytest.raises(FareSourceError, match="credentials missing"):
        AmadeusSource()


def test_amadeus_satisfies_the_faresource_protocol(monkeypatch):
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "secret")
    assert isinstance(AmadeusSource(), FareSource)


def test_credentials_never_appear_in_repr_or_errors(monkeypatch):
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "PUBLICID")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "SUPERSECRET")
    source = AmadeusSource()
    assert "SUPERSECRET" not in repr(source)


class _StubResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_auth_failure_does_not_echo_the_request_body(monkeypatch):
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "SUPERSECRET")
    source = AmadeusSource()

    class _Stub:
        @staticmethod
        def post(*a, **kw):
            return _StubResponse(401, text="unauthorized")

    monkeypatch.setattr(source, "_session", lambda: _Stub)
    with pytest.raises(FareSourceError) as exc:
        source._authenticate()
    assert "SUPERSECRET" not in str(exc.value)
    assert "401" in str(exc.value)


def test_rate_limit_backs_off_then_succeeds(monkeypatch):
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "secret")
    source = AmadeusSource(host=TEST_HOST)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(random, "random", lambda: 0.0)

    calls = {"n": 0}
    payload = fixture("amadeus_offers.json")

    class _Stub:
        @staticmethod
        def post(*a, **kw):
            return _StubResponse(200, {"access_token": "t", "expires_in": 1799})

        @staticmethod
        def get(*a, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                return _StubResponse(429, fixture("amadeus_rate_limited.json"))
            return _StubResponse(200, payload)

    monkeypatch.setattr(source, "_session", lambda: _Stub)
    assert source._get("/v2/shopping/flight-offers", {})["meta"]["count"] == 3
    assert calls["n"] == 3, "must retry rather than give up on 429"


def test_persistent_rate_limit_eventually_raises(monkeypatch):
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "secret")
    source = AmadeusSource(max_retries=2)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    class _Stub:
        @staticmethod
        def post(*a, **kw):
            return _StubResponse(200, {"access_token": "t", "expires_in": 1799})

        @staticmethod
        def get(*a, **kw):
            return _StubResponse(429, {})

    monkeypatch.setattr(source, "_session", lambda: _Stub)
    with pytest.raises(FareSourceError, match="rate limited"):
        source._get("/v2/shopping/flight-offers", {})


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------

class _FakeSource:
    name = "fake"

    def __init__(self, prices=None, fail_on=None):
        self.prices = prices or [5000.0]
        self.fail_on = fail_on or set()

    def search(self, origin, destination, depart, ret=None, cabin="economy",
               adults=1, limit=5):
        if destination in self.fail_on:
            raise FareSourceError("simulated upstream failure")
        return [
            FareObservation(
                origin=origin, destination=destination, depart_date=depart,
                cabin=cabin, price=p, currency="INR",
                observed_at=_dt.datetime(2026, 8, 12, 9, 0), source="fake",
            )
            for p in self.prices
        ]


def test_snapshot_records_observations(store):
    items = [WatchItem("DEL", "BOM", DEPART)]
    assert run_once(items, _FakeSource([5000.0, 6100.0]), store, verbose=False) == 2


def test_one_failing_route_does_not_abort_the_batch(store, capsys):
    items = [WatchItem("DEL", "XXX", DEPART), WatchItem("DEL", "BOM", DEPART)]
    recorded = run_once(items, _FakeSource(fail_on={"XXX"}), store, verbose=True)
    assert recorded == 1, "the healthy route must still be recorded"
    assert "FAIL" in capsys.readouterr().err


def test_past_departures_are_skipped(store):
    items = [WatchItem("DEL", "BOM", _dt.date(2020, 1, 1))]
    assert run_once(items, _FakeSource(), store, verbose=False) == 0


def test_watchlist_loads_from_json(tmp_path):
    path = tmp_path / "wl.json"
    path.write_text(json.dumps([
        {"origin": "DEL", "destination": "BOM", "depart": "2026-12-10"},
        {"origin": "BLR", "destination": "LHR", "depart": "2026-12-20",
         "return": "2027-01-05", "cabin": "business"},
    ]))
    items = load_watchlist(path)
    assert len(items) == 2
    assert items[1].cabin == "business" and items[1].ret == _dt.date(2027, 1, 5)


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------

def _seed_history(store, departures=8, seed=7):
    """Synthetic history: prices decay to a trough then spike near departure."""
    rng = random.Random(seed)
    base = _dt.date(2026, 3, 1)
    for i in range(departures):
        depart = base + _dt.timedelta(days=20 * i)
        floor = 6000 + rng.randint(-500, 500)
        for days_out in (120, 90, 60, 45, 30, 21, 14, 7, 3):
            if days_out > 45:
                price = floor * (1.12 + 0.001 * days_out)
            elif days_out > 14:
                price = floor * 1.0
            else:
                price = floor * (1.0 + (15 - days_out) * 0.08)
            store.record([obs(days_out, round(price, 2), depart=depart)])


def test_backtest_reports_nothing_without_history(store):
    report = bt.backtest_route(store, "DEL", "BOM")
    assert report.n == 0 and "No observations" in report.note


def test_backtest_refuses_until_out_of_sample_baseline_exists(store):
    _seed_history(store, departures=2)
    report = bt.backtest_route(store, "DEL", "BOM")
    assert report.n == 0, "must not simulate without prior completed departures"
    assert "out-of-sample" in report.note


def test_backtest_runs_and_compares_against_naive_baselines(store):
    _seed_history(store, departures=8)
    report = bt.backtest_route(store, "DEL", "BOM")
    assert report.n >= 4
    summary = report.summary()
    assert "book immediately" in summary and "fixed 45d lead" in summary
    assert "VERDICT" in summary


def test_backtest_never_beats_the_oracle(store):
    """The realised minimum is unreachable by construction."""
    _seed_history(store, departures=8)
    for result in bt.backtest_route(store, "DEL", "BOM").results:
        assert result.strategy_price >= result.realised_min
        assert result.regret(result.strategy_price) >= 0


def test_untriggered_departures_still_pay_a_price(store):
    """Pretending the traveller simply did not fly would flatter the model."""
    _seed_history(store, departures=8)
    for result in bt.backtest_route(store, "DEL", "BOM").results:
        assert result.strategy_price is not None


def test_backtest_all_covers_every_stored_route(store):
    _seed_history(store, departures=6)
    store.record([obs(60, 40_000, destination="LHR")])
    assert len(bt.backtest_all(store)) == 2


# --------------------------------------------------------------------------
# Fit -- guards are testable without numpy/scipy
# --------------------------------------------------------------------------

def test_fit_refuses_to_emit_a_model_on_thin_data(store):
    from traveler.calibration import fit as fitmod

    _seed_history(store, departures=3)
    payload = fitmod.fit(store)
    assert payload["coefficients"] == {}
    assert "domestic_india" in payload["skipped"]


def test_fit_writes_a_loadable_payload(store, tmp_path):
    from traveler.calibration import fit as fitmod

    out = tmp_path / "fitted.json"
    fitmod.fit(store, out)
    payload = json.loads(out.read_text())
    assert {"fitted_at", "coefficients", "skipped"} <= set(payload)


def test_fitted_model_loads_from_disk(tmp_path):
    from traveler.market import FittedMarketModel

    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "fitted_at": "2026-08-12",
        "coefficients": {"domestic_india": {"cliff_amplitude": 0.9,
                                            "cliff_decay": 18.0}},
        "observation_counts": {"domestic_india": 640},
    }))
    model = FittedMarketModel.load(path)
    assert "domestic_india(n=640)" in model.provenance


@pytest.mark.live
def test_amadeus_live_smoke():  # pragma: no cover - requires credentials
    """Run manually: pytest -m live with AMADEUS_* set."""
    source = AmadeusSource()
    results = source.search("DEL", "BOM", _dt.date.today() + _dt.timedelta(days=45))
    assert results and all(r.price > 0 for r in results)


def test_backtest_reports_losses_as_prominently_as_wins(store):
    """A win against one baseline must not hide a loss against another."""
    _seed_history(store, departures=10)
    summary = bt.backtest_route(store, "DEL", "BOM").summary()
    if "LOSES" in summary:
        assert "NOT VALIDATED" in summary
    else:
        assert "beats every naive baseline" in summary


def test_verdict_is_never_silently_positive_when_a_baseline_wins():
    from traveler.calibration.backtest import BacktestReport, DepartureResult

    report = BacktestReport(route="X-Y")
    # Strategy pays 100 more than the fixed-lead baseline every time.
    for i in range(5):
        report.results.append(DepartureResult(
            depart_date=f"2026-0{i+1}-01", strategy_price=1_100,
            immediate_price=2_000, fixed_lead_price=1_000,
            realised_min=900, triggered=True,
        ))
    summary = report.summary()
    assert "LOSES" in summary and "NOT VALIDATED" in summary
