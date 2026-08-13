# traveler

A decision engine for booking flights cheaply, from and to India.

**This is decision support, not a booking bot.** It holds no airline
credentials, books nothing, and makes no reservations. It takes a description
of a trip and emits a ranked, costed strategy that a human executes.

## Install and run

The core engine is pure standard library, Python 3.10+.

```bash
python3 -m traveler --from BLR --to LHR \
    --depart 2026-12-10 --return 2026-12-28 \
    --cabin business --fare 185000 --baseline 210000 \
    --flex-days 5 --origin-flex --cards infinia,scapia \
    --points 400000 --programmes KrisFlyer
```

Add `--json` for machine-readable output.

```bash
python3 -m pytest tests/ -q          # 189 tests
pip install -e ".[calibrate]"        # numpy/scipy/requests, calibration only
```

---

## How much to trust the output

This matters more than the feature list, so it comes first. The engine's
inputs fall into three tiers, and it is honest about which is which — every
plan prints its model provenance.

### 🟢 Sourced facts

DGCA look-in window (48h, ≥7d domestic / ≥15d international, airline-direct
only), DGCA refund timelines, US DOT significant-change thresholds (3h/6h),
IndiGo 6E Fare Hold pricing, UDAN fare cap, consolidator discount ranges,
self-transfer buffers, bank offer caps. All carry a `SNAPSHOT_DATE` in
`knowledge.py` and can be re-verified.

### 🟡 Direction right, precision is judgment

Booking windows and tactic priors. The published studies genuinely
contradict each other, so the boundaries are centres of mass. **Trust the
ordering of tactics; treat the magnitudes as rough.**

### 🔴 Hand-authored, unvalidated

The price-multiplier curve shape, volatility decay, option haircuts, the
seasonality multipliers. These are plausible, internally consistent, and
**not derived from data**. That is what `traveler.calibration` exists to fix.

Until `fit` has run against real observations, the engine reports
`provenance: hand-authored priors (uncalibrated)` on every plan, and every
monetary output is an `Estimate(low, mid, high)` band rather than a point —
because false precision is how an unvalidated model gets trusted.

---

## What it computes

Six models, composed by `engine.plan_trip`.

### `market.py` — descriptive: what fares *do*

Price multiplier by days-out (early plateau → trough → last-minute cliff),
volatility, seasonality. Strictly separated from prescription so a fitted
model can replace the hand-authored one without touching recommendation
logic. Two implementations behind a `MarketModel` protocol:
`DefaultMarketModel` (ships) and `FittedMarketModel` (loads calibration
output, falls back per-route-class where data was thin).

`MarketRegime` is a parameter, not a constant. It defaults to `RISING` for
the 2026 ATF shock, which shifts the window earlier. Set it to `stable` when
conditions change rather than editing the curve.

### `seasonality.py` — departure-date demand

Festival and peak calendar (Diwali, Christmas–New Year, summer holidays,
Durga Puja, Eid) scaled by route sensitivity, since festivals move Indian
domestic demand far more than long-haul. Overlapping peaks take the maximum
rather than compounding. **Distinct from the days-out effect** — a December
24 departure and a February 24 departure at the same lead time are not the
same booking.

Festival dates are lunar and move. The calendar covers 2026–2028 and says so
explicitly past that horizon rather than silently returning 1.0.

### `timing.py` — prescriptive: what to do about it

Urgency verdict and trigger price. The trigger is derived from baseline ×
curve position × season, so the target loosens honestly as departure
approaches instead of anchoring on an unreachable trough. Peak departures are
pushed one step more urgent.

### `options.py` — pricing optionality

The central idea. Every booking decision is a trade in options, and most
travellers give theirs away unpriced:

| Instrument | What it is | Cost |
|---|---|---|
| Fare hold | a call on the fare | ₹99 / ₹199 per pax |
| DGCA 48h look-in | a free put on the booking | ₹0, airline-direct only |
| Award seat, free redeposit | a free option on the trip | ₹0 |
| Cheap non-refundable fare | you *sold* optionality | you were paid |
| OTA coupon | you sold the DGCA put | you were paid the coupon |

A hold's value is `E[max(0, ΔP)]` — the upward half of the expected move,
discounted by the chance you take the trip. `coupon_versus_lookin` resolves
the trade travellers get wrong most often.

### `payments.py` — the stack, with units kept apart

A constrained optimisation, not a lookup: offers are capped, mutually
exclusive and day-of-week gated, so the biggest headline routinely loses —
25% capped at ₹3,000 is **1.6%** on a ₹185,000 fare.

Three kinds of value are tracked and **never summed into a headline**:

- **cash off** — money
- **points value** — speculative, depends on redeeming well
- **option value** — not money at all

They combine only inside `comparable_total()`, with explicit weights, purely
to rank channels. Set `points_weight=0` if you distrust points and the
ranking changes — which is the point.

Points valuation is an explicit chain: `spend → points earned → monthly cap →
rupee value → realisation haircut`. Collapsing that into one "percent back"
figure is how an earlier version claimed 16.5% uncapped value-back on an
HDFC Infinia.

### `tactics.py` — ranked by expected value

Priors conditioned on the trip: consolidator fares score *higher* when dates
are hard (the one lever that works without flexibility); error fares do not
appear at all without both flexibility and the ability to commit fast.

Tactics carry an `exclusivity_group`. One ticket is bought through exactly
one channel and routed exactly one way, so grouped tactics contribute only
their best member; ungrouped ones genuinely stack and compose
multiplicatively.

### `rights.py` — protections that carry money

The schedule-change lever is the underused one: a significant retiming
entitles you to free rebooking *or* a full refund to original payment, so
booking early carries an embedded free option most travellers never exercise.

---

## Calibration

The path from hand-authored to measured:

```
seed / snapshot  →  store  →  backtest / fit  →  FittedMarketModel
```

**Amadeus Self-Service was decommissioned on 17 July 2026** — portal offline,
keys disabled, no new registration. `AmadeusSource` is retained because it
still works against Amadeus *Enterprise* and is the reference implementation
of the `FareSource` seam, but new deployments use `GoogleFlightsSource`.

### Solving the cold start

Polling one itinerary a day fills the booking curve at one point per day, so
a naive setup waits months. Three things collapse that:

**Harvest history rather than accumulate it.** Google publishes a price
series per route, exposed as `price_insights.price_history`. One call returns
months of curve data that was never polled. It is best-effort — Google only
generates insights for routes it considers popular — so every path degrades
to offers-only rather than failing.

**Sweep departure dates, not just days.** The curve is a function of
`days_out`, so `--grid` fans one route across many departures in a single run
and fills the whole axis at once. In tests this recovers a known curve
(`decay=18`, `amplitude=1.0`) to R² > 0.8 from **one sweep**, where 30 days
of longitudinal polling on six itineraries still falls short of the
threshold.

**Seed from a published dataset.** The Kaggle EaseMyTrip set (300,261 Indian
domestic rows with a `days_left` column) teaches the cliff region at a sample
size live polling will not reach for a year.

```bash
# 0. Optional: seed the cliff from a static dataset (download it yourself;
#    Kaggle needs an account).
python3 -m traveler.calibration.importers Clean_Dataset.csv

# 1. Plan the sweep before spending metered calls.
python3 -m traveler.calibration.snapshot --watchlist watchlist.json \
    --grid 20 --stride 14 --harvest-history --dry-run

# 2. Collect. Cross-sectional sweep + history harvest.
SEARCHAPI_API_KEY=... python3 -m traveler.calibration.snapshot \
    --watchlist watchlist.json --grid 20 --stride 14 \
    --harvest-history --max-calls 100

# 3. Ask whether the strategy actually beats naive baselines.
python3 -m traveler.calibration.backtest --route DEL-BOM

# 4. Fit.
python3 -m traveler.calibration.fit --out fitted_market.json
```

### Not all observations are equal

A live offer is a price you could have transacted; Google's history is an
aggregate; a 2022 scrape describes a market that no longer exists. Three
guards keep that honest:

- **Recency and quality weighting** — source quality decayed with a 365-day
  half-life, so a 2022 row counts ~2% of a fresh one.
- **Bulk-import share cap** — weighting alone is not enough, since 300,000
  stale rows at 2% still outweigh 500 live ones. No bulk-imported source may
  exceed 50% of total weight. Deliberately *not* applied to live feeds: an
  earlier version capped Google's history too and threw away the very data
  that makes cold-start fast.
- **Coverage guard** — a dataset stopping at 49 days out cannot speak about
  200 days out, so `FittedMarketModel` falls back to hand-authored values
  outside the fitted window rather than extrapolating.

### The fit audits the seasonality model

Cross-sectional data confounds lead time with season, so the season
multiplier is divided out before fitting. But that only helps if the festival
calendar is approximately right — dividing out an effect the data does not
contain *injects* error. Measured on synthetic data:

| data has season | deconfound | recovered decay (true 18) | R² |
|---|---|---|---|
| yes | yes | 22.9 | 0.907 |
| yes | no | 39.5 | 0.101 |
| no | yes | 151.1 | 0.209 |
| no | no | 22.9 | 0.907 |

So `fit` runs both and keeps the better one. Which wins is itself a
diagnostic: if removing the modelled seasonality makes the fit *worse*, the
calendar in `knowledge.py` does not match that route class, and the fit says
so in a `SEASONALITY WARNING`.

`watchlist.json` is a list of itineraries:

```json
[{"origin": "DEL", "destination": "BOM", "depart": "2026-12-10"},
 {"origin": "BLR", "destination": "LHR", "depart": "2026-12-20",
  "return": "2027-01-05", "cabin": "business"}]
```

**The backtest is designed to be able to fail.** It compares against
*book-immediately* and *book at a fixed 45-day lead*, reports losses as
prominently as wins, and prints `VERDICT: NOT VALIDATED` when a naive
baseline beats the strategy. Lookahead bias is avoided by estimating the
baseline only from departures that had already completed at simulated
decision time — using each departure's own realised minimum would let the
strategy see the future and make the whole exercise worthless.

`fit` refuses to emit coefficients below 200 observations and 5 distinct
departures per route class. Many observations of one departure is one data
point about the booking curve, not five hundred. A confidently wrong fitted
curve is worse than an honestly uncalibrated one.

Amadeus credentials come from the environment, are never written to the
store, never logged, and never appear in `repr()` or exception messages.

---

## Testing

189 tests, biased toward **invariants over fixed values**. A test asserting
the curve bottoms out exactly where it was coded to bottom out is a
change-detector, not validation. What is asserted instead: monotonicity
through the cliff, volatility strictly decreasing with lead time, portfolio
estimates never exceeding the fare, estimate bounds ordered, seasonality
bounded, the engine never crashing across a swept input space.

Both fare sources are tested against recorded fixtures (`tests/fixtures/`)
covering auth failure, normal offers, empty results, malformed entries, rate
limiting, and — for Google Flights — responses with and without
`price_insights`. `test_data_sources.py` additionally checks the claims this
rests on: that a grid sweep does not collapse to all-ones ratios, that a
stale seed cannot outvote live data, that the coverage guard refuses to
extrapolate, and that a known curve is recovered from one sweep. Live tests
are marked and deselected by default:

```bash
pytest -m live          # needs a real SEARCHAPI_API_KEY
```

---

## Limitations

- **The market model is uncalibrated as shipped.** Rankings are more reliable
  than rupee figures until you run `fit`.
- `OTA_OFFERS` is the *shape* of the market, not a live feed. Bank offers
  rotate constantly — re-verify before booking.
- UDAN sector membership cannot be checked offline; the engine flags trips
  whose *shape* could qualify and tells you to check the operational list.
- Route classification covers the airports Indian travellers actually use,
  not all of them. Unknown airports fall back to long-haul international —
  the conservative choice, since it widens the window.
- Festival dates need extending past 2028.
- No live booking, and none is planned. This stays decision support.
