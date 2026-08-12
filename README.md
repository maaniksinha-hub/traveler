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
python3 -m pytest tests/ -q          # 145 tests
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
snapshot  →  store  →  backtest / fit  →  FittedMarketModel
```

```bash
# 1. Accumulate observations. No API hands you a back catalogue, so this
#    only pays off by running daily for months. Put it in cron.
AMADEUS_CLIENT_ID=... AMADEUS_CLIENT_SECRET=... \
    python3 -m traveler.calibration.snapshot --watchlist watchlist.json

# 2. Ask whether the strategy actually beats naive baselines.
python3 -m traveler.calibration.backtest --route DEL-BOM

# 3. Fit the curve once there is enough data.
python3 -m traveler.calibration.fit --out fitted_market.json
```

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

145 tests, biased toward **invariants over fixed values**. A test asserting
the curve bottoms out exactly where it was coded to bottom out is a
change-detector, not validation. What is asserted instead: monotonicity
through the cliff, volatility strictly decreasing with lead time, portfolio
estimates never exceeding the fare, estimate bounds ordered, seasonality
bounded, the engine never crashing across a swept input space.

Amadeus is tested against recorded fixtures (`tests/fixtures/`) covering
auth failure, normal offers, empty results, malformed entries and rate
limiting. Live tests are marked and deselected by default:

```bash
pytest -m live          # needs real AMADEUS_* credentials
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
