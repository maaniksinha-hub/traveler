// Golden-trip verification: runs the same inputs through the JS port and
// prints a summary in the same shape as `python3 -m traveler --json`, so the
// two can be diffed by hand (or by a wrapper script) for parity.
//
// The `days_out` field is a deliberate, disclosed exception -- see the note
// above `planTrip` in traveler.mjs. Everything else should match closely
// (small float rounding differences are expected and fine).

import {
  makeTrip, makeFlexibility, makeTraveller, mkDate, planTrip, toSummary,
  CARD_CATALOGUE,
} from "./traveler.mjs";

function run(name, tripArgs, travellerArgs, opts) {
  const trip = makeTrip(tripArgs);
  const traveller = makeTraveller(travellerArgs);
  const plan = planTrip(trip, traveller, opts);
  console.log(`\n=== ${name} ===`);
  console.log(JSON.stringify(toSummary(plan, opts.today), null, 2));
}

const TODAY = mkDate("2026-08-12");

// Scenario 1: BLR-LHR business, flexible, holds Infinia + points.
run(
  "BLR-LHR business (flexible, Infinia + points)",
  {
    origin: "BLR", destination: "LHR", depart: mkDate("2026-12-10"),
    ret: mkDate("2026-12-28"), cabin: "business",
    flexibility: makeFlexibility({ dateFlexDays: 5, originFlex: true }),
    observedFare: 185000, baselineFare: 210000,
  },
  { cards: [CARD_CATALOGUE.infinia], availablePoints: 400000, loyaltyProgrammes: ["KrisFlyer"] },
  { today: TODAY, regime: "rising" }
);

// Scenario 2: DEL-BOM domestic, cheap, student.
run(
  "DEL-BOM domestic (student)",
  {
    origin: "DEL", destination: "BOM", depart: mkDate("2026-09-28"),
    observedFare: 9500, baselineFare: 6000,
    flexibility: makeFlexibility({ dateFlexDays: 3 }),
  },
  { isStudent: true, cards: [CARD_CATALOGUE.infinia] },
  { today: TODAY, regime: "rising" }
);

// Scenario 3: DEL-BOM Diwali-window departure (seasonality check).
run(
  "DEL-BOM Diwali window",
  {
    origin: "DEL", destination: "BOM", depart: mkDate("2026-11-08"),
    observedFare: 11000, baselineFare: 7000,
    flexibility: makeFlexibility({ dateFlexDays: 3 }),
  },
  {},
  { today: TODAY, regime: "rising" }
);
