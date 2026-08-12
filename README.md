# traveler

A decision engine for booking flights cheaply, from and to India.

**This is decision support, not a booking bot.** It holds no airline
credentials, calls no fare APIs, and books nothing. It takes a description of
a trip and emits a ranked, costed strategy that a human executes. Anything
claiming to automatically book the cheapest fare would need live GDS access
and would still be wrong most of the time — the honest artefact is a model
that tells you *which levers apply to this trip and what each is worth*.

## Install and run

Pure standard library; Python 3.10+. No dependencies to install.

```bash
python3 -m traveler --from BLR --to LHR \
    --depart 2026-12-10 --return 2026-12-28 \
    --cabin business --fare 185000 --baseline 210000 \
    --flex-days 5 --origin-flex --cards infinia,scapia \
    --points 400000 --programmes KrisFlyer
```

Add `--json` for machine-readable output.

```bash
python3 -m pytest tests/ -q     # 45 tests
```

## What it actually computes

Five models, each in its own module, composed by `engine.plan_trip`.

### 1. Timing (`timing.py`)

Expected fare as a multiple of the trough price, as a function of
days-before-departure: an early plateau, a trough (the booking window), then a
steep last-minute cliff. Produces a **band and an urgency signal**, not a
"best day to book" — the published studies contradict each other, and that
disagreement is itself the finding.

`MarketRegime` is a parameter, not a constant. It defaults to `RISING` to
reflect the 2026 ATF shock, which shifts the whole window earlier: when the
drift is upward, waiting costs more than it saves. Set it to `stable` or
`falling` when conditions change rather than editing the curve.

### 2. Optionality (`options.py`)

The central idea. Every booking decision is a trade in options, and most
travellers give theirs away without pricing them:

| Instrument | What it is | Cost |
|---|---|---|
| Fare hold | a call on the fare | ₹99 / ₹199 |
| DGCA 48h look-in | a free put on the booking | ₹0 (airline-direct only) |
| Award seat with free redeposit | a free option on the whole trip | ₹0 |
| Cheap non-refundable fare | you *sold* optionality | you were paid |
| OTA coupon | you sold the DGCA put | you were paid the coupon |

A fare hold's value is `E[max(0, ΔP)]` — the upward half of the expected move,
discounted by the chance you actually take the trip. `coupon_versus_lookin`
resolves the trade travellers get wrong most often: a visible coupon beats an
invisible option in intuition, frequently not in value.

### 3. Payment stack (`payments.py`)

A small constrained optimisation, not a lookup. Bank offers are capped,
mutually exclusive and day-of-week gated, so the biggest headline percentage
routinely loses to a smaller uncapped one — 25% capped at ₹3,000 on a
₹200,000 fare is 1.5%. The optimiser also charges the OTA route for the DGCA
window it forfeits, so channels compete on net value rather than sticker
discount. It computes the true cost of "no-cost" EMI (GST on notional
interest plus processing fee) and picks the right card for foreign
point-of-sale purchases.

### 4. Tactics (`tactics.py`)

Each lever carries a prior expected saving and a hit probability conditioned
on the trip, so the output is ranked by expected value rather than being a
generic checklist. Consolidator fares score *higher* when dates are hard —
they are the one lever that works without flexibility. Error fares require
both flexibility and the ability to commit fast, so they simply do not appear
for a fixed-date booking.

`portfolio_estimate` combines multiplicatively on the remaining fare: you
cannot stack a consolidator fare, an award and an error fare on one ticket,
so summing them would be nonsense.

### 5. Rights (`rights.py`)

Passenger rights that carry money. The schedule-change lever is the
underused one: a significant retiming entitles you to free rebooking *or* a
full refund to original payment, which means booking early carries an
embedded free option most travellers never exercise. Includes the US DOT
3h/6h significant-change thresholds when the itinerary touches the US.

## Design notes

**`knowledge.py` is data, logic lives elsewhere.** Every constant is a fact
about the world that decays — fare caps, offer percentages, regulatory
thresholds. It carries a `SNAPSHOT_DATE` and each block cites where it came
from, so it can be re-verified rather than trusted indefinitely. The engine's
reasoning does not change when the data does.

**Unknown airports fall back to long-haul international.** The conservative
choice: it widens the booking window and surfaces more checks rather than
fewer.

**Absolute numbers come from the user.** There is no live pricing here, so
`--fare` and `--baseline` are user-supplied and everything monetary is derived
from them. Without them the engine still runs and gives qualitative guidance,
and flags that it is doing so.

## Limitations

- No live fares. Route classification covers the airports Indian travellers
  actually use, not all of them.
- `OTA_OFFERS` is the *shape* of the market, not a live feed. Bank offers
  rotate constantly; re-verify before booking.
- UDAN sector membership cannot be checked offline. The engine flags trips
  whose *shape* could qualify and tells you to check the operational route
  list.
- Tactic priors are conservative point estimates standing in for wide ranges.
  Treat the ranking as reliable and the rupee figures as indicative.
