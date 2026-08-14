# Port notes

`web/traveler.html` is a **hand-synced parallel implementation** of the
Python `traveler` engine, built because a published Artifact runs in a
browser sandbox and cannot execute Python. It is not a live wrapper — it
will not pick up future changes to `traveler/*.py` automatically. Re-sync it
by diffing this file against the Python source below, section by section.

## Build

Nothing in `web/traveler.html` is hand-edited directly — it's generated:

```bash
python3 web/build/assemble.py
```

Source parts, combined in this order:

| Part | Role |
|---|---|
| `web/build/body.html` | markup, `<meta charset>`, `<title>` |
| `web/build/style.css.tmpl` | design tokens + components, `{{FONT}}` placeholders |
| `web/fonts/*.b64` | IBM Plex Serif/Sans/Mono, base64, substituted into the CSS |
| `web/traveler.mjs` | the ported decision engine (import line stripped; `AIRPORTS_RAW` inlined from `web/data/airports-data.mjs` instead of imported) |
| `web/build/app-ui.js` | form handling + results rendering |

Regenerate the airport data and fonts independently if they go stale:

```bash
python3 -m traveler.airports --refresh   # updates traveler/data/airports.csv
# then re-run the generator that produced web/data/airports-data.mjs
# (see the one-liner in its own header comment)
```

Font files came from `raw.githubusercontent.com/IBM/plex` (SIL OFL), fetched
once and committed as `.woff2` + `.b64` under `web/fonts/`.

## Module mapping

Every section in `web/traveler.mjs` is labeled with a banner comment naming
the Python file it mirrors:

| JS section | Python source | Notes |
|---|---|---|
| `knowledge.js` block | `traveler/knowledge.py` | every constant verbatim |
| airports / `REGISTRY` | `traveler/airports.py` + `traveler/data/airports.csv` | full 4,085-row registry inlined, not just the hand-maintained fallback tables |
| `models.js` block | `traveler/models.py` | camelCase field names throughout |
| `routing.js` block | `traveler/routing.py` | |
| `market.js` block | `traveler/market.py` | **`FittedMarketModel` not ported** — out of scope, see below |
| `seasonality.js` block | `traveler/seasonality.py` | |
| `timing.js` block | `traveler/timing.py` | |
| `options.js` block | `traveler/options.py` | |
| `payments.js` block | `traveler/payments.py` | |
| `tactics.js` block | `traveler/tactics.py` | |
| `rights.js` block | `traveler/rights.py` | |
| `engine.js` block | `traveler/engine.py` | |
| `CARD_CATALOGUE` | `traveler/cli.py` | the same six real cards |

## Verified against the Python engine

`web/verify.mjs` runs three golden trips through the JS port and prints the
same shape `python3 -m traveler --json` does, for manual diffing:

```bash
node web/verify.mjs
python3 -m traveler --from BLR --to LHR --depart 2026-12-10 --return 2026-12-28 \
  --cabin business --fare 185000 --baseline 210000 --flex-days 5 --origin-flex \
  --cards infinia --points 400000 --programmes KrisFlyer --today 2026-08-12 --json
# ...and the other two scenarios in verify.mjs
```

Result at time of writing: every field matched except two disclosed,
understood differences —

1. **`days_out`** differs by however many days have passed in the real world
   since `--today` was typed. This is a **Python bug, not a port
   divergence**: `engine.summarise()` and `cli.py`'s `render()` both call
   `trip.days_out()` with no argument, which falls back to
   `datetime.date.today()` — the real wall-clock date — rather than the
   `--today` value used for the rest of the plan. Every other figure
   (urgency, trigger price, tactics, payment) correctly uses the injected
   `today`; only this one displayed number doesn't. The JS port's
   `toSummary(plan, today)` requires `today` explicitly and computes
   `days_out` from it, which is what the Python code evidently intended.
   Worth fixing upstream in `traveler/engine.py` and `traveler/cli.py` at
   some point — not done here since this task was additive-only.

2. **A single 0.01 INR difference** in one estimate's high band, from a
   rounding-tie edge case: `742.5 × 1.45 = 1076.625` exactly, and Python's
   banker's rounding vs. JS's round-half-up disagree at that exact halfway
   point. Everything upstream matched to full float precision. Negligible.

## Known, disclosed gaps

- **No live fare data.** Same as the CLI without `--fare`/`--baseline` — the
  page is qualitative-only until the user types numbers in.
- **`FittedMarketModel` is not ported.** The web version always runs on
  `DefaultMarketModel` (hand-authored, uncalibrated priors), same as the
  CLI's default. The whole `traveler.calibration` package (snapshot / fit /
  backtest / importers / fast-flights / Google Flights sources) is
  CLI/Python-only and out of scope here. The provenance banner on every
  result says so.
- **UI structure is a pragmatic simplification of the original plan.** The
  plan described five numbered wizard steps; what's built is a single-scroll
  sectioned form with one collapsed accordion each for Wallet and Advanced.
  Same progressive-disclosure goal (required fields up front, optional
  detail collapsed), fewer moving parts, no step-transition animation needed
  as a result — the animation budget went to the results reveal instead.
- **This will drift.** Any future change to `traveler/*.py` needs a matching
  manual edit here. There is no test that catches a silent divergence beyond
  re-running `verify.mjs` against the CLI by hand.
