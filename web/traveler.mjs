// traveler.mjs
//
// A faithful JavaScript port of the Python `traveler` decision engine, for
// use in a browser Artifact that cannot execute Python. See PORT_NOTES.md
// for exactly which Python file each section below mirrors, and for the one
// deliberate, disclosed deviation from the Python source (the days_out field
// in the summary -- see the note above `toSummary`).
//
// This is a hand-synced parallel implementation, not a live wrapper. It will
// not automatically pick up future changes to the Python engine.
//
// Pure, dependency-free ES module. Runs under Node (for verification) and in
// a browser (once inlined into the Artifact).

import { AIRPORTS_RAW } from "./data/airports-data.mjs";

// ===========================================================================
// knowledge.js -- mirrors traveler/knowledge.py. Data only, verbatim.
// ===========================================================================

export const SNAPSHOT_DATE = "2026-08-12";

// Hand-maintained fallback tables. In the browser the full OurAirports
// registry below is always present, so these mostly matter for parity with
// the Python module's documented "degrades, does not break" behaviour.
const INDIAN_AIRPORTS = new Set(`
  DEL BOM BLR MAA HYD CCU COK AMD PNQ GOI GOX JAI LKO IXC TRV IXM CJB
  VNS PAT BBI GAU IXB IXR NAG IDR BHO RPR SXR IXJ ATQ DED HBX IXE IXZ
  STV RAJ BDQ UDR JDH IXA SHL IMF DIB IXS TIR VTZ VGA MYQ TRZ IXL AGX
  HGI KNU GWL JLR BHU PGH SLV DHM SXV CDP RJA JGB PYG DBR HSS
`.split(/\s+/).filter(Boolean));

const MAINSTREAM_AIRPORTS = new Set(`
  DEL BOM BLR MAA HYD CCU COK AMD PNQ GOI GOX JAI LKO IXC TRV NAG IDR
  BBI GAU PAT VNS SXR ATQ RPR BHO IXR JDH VTZ TIR CJB IXM IXE IXB IXJ
  IXA IMF SHL DIB TRZ UDR BDQ RAJ STV IXZ IXL DED HBX
`.split(/\s+/).filter(Boolean));

const SHORT_HAUL_COUNTRIES = new Set([
  "AE", "OM", "QA", "BH", "KW", "SA", "LK", "NP", "BD", "BT", "MV", "MM",
  "TH", "MY", "SG", "PK", "AF", "UZ", "KZ",
]);

const AIRPORT_COUNTRY = {
  DXB: "AE", AUH: "AE", SHJ: "AE", DOH: "QA", MCT: "OM",
  BAH: "BH", KWI: "KW", RUH: "SA", JED: "SA",
  CMB: "LK", KTM: "NP", DAC: "BD", PBH: "BT", MLE: "MV",
  BKK: "TH", DMK: "TH", HKT: "TH", KUL: "MY", SIN: "SG",
  RGN: "MM", KHI: "PK", LHE: "PK", TAS: "UZ",
  LHR: "GB", LGW: "GB", CDG: "FR", FRA: "DE", MUC: "DE",
  AMS: "NL", ZRH: "CH", FCO: "IT", MAD: "ES", IST: "TR",
  JFK: "US", EWR: "US", SFO: "US", ORD: "US", IAD: "US",
  LAX: "US", MIA: "US", BOS: "US", SEA: "US", ATL: "US",
  DFW: "US", PHX: "US", DEN: "US", LAS: "US", MCO: "US",
  PHL: "US", CLT: "US", DTW: "US", MSP: "US", SLC: "US",
  AUS: "US", IAH: "US", SAN: "US", TPA: "US", BWI: "US",
  DCA: "US", RDU: "US", PDX: "US", STL: "US", MCI: "US",
  YYZ: "CA", YVR: "CA", YUL: "CA", SYD: "AU", MEL: "AU",
  BNE: "AU", PER: "AU", AKL: "NZ",
  NRT: "JP", HND: "JP", ICN: "KR", HKG: "HK", PEK: "CN",
  PVG: "CN", JNB: "ZA", NBO: "KE", CAI: "EG",
};

const BOOKING_WINDOWS = {
  domestic_india: [21, 56],
  short_haul_intl: [42, 70],
  long_haul_intl: [60, 150],
  foreign_domestic: [21, 60],
};

const LAST_MINUTE_CLIFF_DAYS = {
  domestic_india: 14,
  short_haul_intl: 21,
  long_haul_intl: 28,
  foreign_domestic: 14,
};

const DGCA_LOOKIN_HOURS = 48;
const DGCA_LOOKIN_MIN_DAYS_DOMESTIC = 7;
const DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL = 15;
const DGCA_REFUND_DAYS_DIRECT = 7;
const DGCA_REFUND_DAYS_AGENT = 14;
const DOT_SIGNIFICANT_DELAY_DOMESTIC_H = 3;
const DOT_SIGNIFICANT_DELAY_INTERNATIONAL_H = 6;
export const UDAN_FARE_CAP_INR = 2500;

const FARE_HOLD_PRODUCTS = {
  indigo_6e_domestic: { label: "IndiGo 6E Fare Hold (domestic)", cost_inr: 99, hold_hours: 72 },
  indigo_6e_international: { label: "IndiGo 6E Fare Hold (international)", cost_inr: 199, hold_hours: 72 },
};

// Referenced only in static tactic copy in the Python source; kept for
// PORT_NOTES fidelity even though nothing here branches on them directly.
export const SURCHARGE_FREE_PROGRAMMES = new Set(["KrisFlyer", "MileagePlus", "Aeroplan", "Miles&Smiles", "Atmos"]);
export const SURCHARGE_HEAVY_PROGRAMMES = new Set(["Executive Club", "Flying Club", "Miles & More", "Flying Blue", "SKYPASS"]);

const OTA_OFFERS = [
  { platform: "Cleartrip", issuer: "Bank of Baroda", pct: 25.0, cap_inr: 3000, days: null },
  { platform: "Goibibo", issuer: "Bank of Baroda", pct: 15.0, cap_inr: 2000, days: [4, 5] },
  { platform: "EaseMyTrip", issuer: "Bank of Baroda", pct: 15.0, cap_inr: 2000, days: [2, 3] },
  { platform: "EaseMyTrip", issuer: "HSBC", pct: 15.0, cap_inr: 2500, days: null },
  { platform: "MakeMyTrip", issuer: "SBI debit", pct: 10.0, cap_inr: 1500, days: [1, 3, 4] },
  { platform: "MakeMyTrip", issuer: "Visa Signature", pct: 10.0, cap_inr: 1800, days: null },
];

const DEFAULT_FOREX_MARKUP_PCT = 3.5;
export const HDFC_SMARTBUY_MONTHLY_POINTS_CAP = 50_000;
const DCC_PENALTY_PCT = 5.0;
const EMI_GST_PCT = 18.0;
const EMI_TYPICAL_PROCESSING_FEE_INR = 199.0;
const EMI_FORECLOSURE_PCT = 3.0;

const CATEGORY_FARES = {
  armed_forces: {
    label: "Armed forces / defence fare", best_pct: 50.0, typical_pct: 25.0,
    note: "IndiGo up to 50% off base fare; Air India 25-35% on select international sectors; Akasa 10% on Saver. Airline site only.",
  },
  student: {
    label: "Student fare", best_pct: 10.0, typical_pct: 8.0,
    note: "IndiGo ~10% off base + 10kg extra baggage, ages 12-25. Also Akasa, Air India Express, SpiceJet. Valid student ID.",
  },
  senior: {
    label: "Senior citizen fare", best_pct: 8.0, typical_pct: 6.0,
    note: "Ages 60+, auto-applied with Aadhaar on airline sites.",
  },
};

const RECURRING_PEAKS = [
  [[5, 10], [6, 30], 1.30, "summer school holidays"],
  [[12, 18], [1, 5], 1.45, "Christmas / New Year"],
  [[10, 1], [10, 15], 1.10, "early-October shoulder peak"],
];

const FESTIVAL_PEAKS = {
  2026: [
    [[10, 15], [10, 22], 1.25, "Durga Puja / Dussehra"],
    [[11, 3], [11, 13], 1.40, "Diwali"],
    [[3, 17], [3, 23], 1.15, "Eid al-Fitr"],
  ],
  2027: [
    [[10, 5], [10, 12], 1.25, "Durga Puja / Dussehra"],
    [[10, 24], [11, 3], 1.40, "Diwali"],
    [[3, 7], [3, 13], 1.15, "Eid al-Fitr"],
  ],
  2028: [
    [[9, 23], [9, 30], 1.25, "Durga Puja / Dussehra"],
    [[11, 11], [11, 21], 1.40, "Diwali"],
    [[2, 24], [3, 1], 1.15, "Eid al-Fitr"],
  ],
};

const LAST_CALENDARED_YEAR = Math.max(...Object.keys(FESTIVAL_PEAKS).map(Number));

const SEASON_SENSITIVITY = {
  domestic_india: 1.0, short_haul_intl: 0.7, long_haul_intl: 0.5, foreign_domestic: 0.3,
};

const TACTIC_PRIORS = {
  consolidator: 0.35, error_fare: 0.45, award_redemption: 0.55, split_ticket: 0.28,
  positioning: 0.20, open_jaw: 0.12, point_of_sale: 0.12, udan_cap: 0.30,
  category_fare: 0.15, off_peak_shift: 0.15, tier2_airport: 0.10, stopover_program: 0.08,
};

// ===========================================================================
// Small shared helpers -- date arithmetic and number formatting. No Python
// equivalent file; these exist because JS has no built-in date type as
// convenient as Python's `datetime.date`.
// ===========================================================================

export function mkDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function todayUTC() {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

function daysBetween(a, b) {
  return Math.round((a.getTime() - b.getTime()) / 86_400_000);
}

function roundTo(x, n) {
  const f = 10 ** n;
  return Math.round(x * f) / f;
}

// Python's `{:,.0f}` -- standard 3-digit comma grouping, not Indian lakh
// grouping. Deliberately matched to the Python source's exact output shape
// for golden-trip diffing, even though the product is India-focused.
function fmt(n) {
  return Math.round(n).toLocaleString("en-US");
}

// Python weekday: Monday=0 .. Sunday=6. JS Date#getUTCDay: Sunday=0 .. Sat=6.
function pythonWeekday(date) {
  return (date.getUTCDay() + 6) % 7;
}

// ===========================================================================
// models.js -- mirrors traveler/models.py
// ===========================================================================

export function makeEstimate(low, mid, high) {
  if (!(low <= mid && mid <= high)) {
    throw new Error(`Estimate must satisfy low <= mid <= high, got (${low}, ${mid}, ${high})`);
  }
  return { low, mid, high };
}

export function estimateAround(mid, spread = 0.45) {
  return makeEstimate(mid * (1.0 - spread), mid, mid * (1.0 + spread));
}

export function estimateScaled(e, factor) {
  if (factor < 0) throw new Error("factor must be non-negative");
  return makeEstimate(e.low * factor, e.mid * factor, e.high * factor);
}

export function estStr(e) {
  if (e.low === e.high) return fmt(e.mid);
  return `${fmt(e.low)}-${fmt(e.high)}`;
}

export function cabinIsPremium(cabin) {
  return cabin === "premium_economy" || cabin === "business" || cabin === "first";
}

export function makeFlexibility({
  dateFlexDays = 0, destinationFlex = false, acceptsSelfTransfer = false,
  canBookFast = false, originFlex = false, hardDates = false,
} = {}) {
  if (dateFlexDays < 0) throw new Error("date_flex_days cannot be negative");
  if (hardDates && dateFlexDays) {
    throw new Error(
      `hard_dates=True contradicts date_flex_days=${dateFlexDays}. Set one or ` +
      `the other -- silently resolving this hid real modelling errors.`
    );
  }
  const score = hardDates ? 0.0 : (
    [Math.min(dateFlexDays, 14) / 14.0, destinationFlex ? 1 : 0, acceptsSelfTransfer ? 1 : 0,
      canBookFast ? 1 : 0, originFlex ? 1 : 0].reduce((a, b) => a + b, 0) / 5
  );
  return { dateFlexDays, destinationFlex, acceptsSelfTransfer, canBookFast, originFlex, hardDates, score };
}

export function makeRewardProgramme({
  basePointsPer100, pointValueInr = 1.0, realizationRate = 0.6,
  portalMultiplier = 1.0, monthlyPointsCap = null,
}) {
  return { basePointsPer100, pointValueInr, realizationRate, portalMultiplier, monthlyPointsCap };
}

function pointsEarned(prog, fareInr, viaPortal = true) {
  const multiplier = viaPortal ? prog.portalMultiplier : 1.0;
  const raw = (fareInr / 100.0) * prog.basePointsPer100 * multiplier;
  return prog.monthlyPointsCap != null ? Math.min(raw, prog.monthlyPointsCap) : raw;
}

function progValueInr(prog, fareInr, viaPortal = true) {
  return pointsEarned(prog, fareInr, viaPortal) * prog.pointValueInr * prog.realizationRate;
}

function capBinds(prog, fareInr, viaPortal = true) {
  if (prog.monthlyPointsCap == null) return false;
  const multiplier = viaPortal ? prog.portalMultiplier : 1.0;
  const raw = (fareInr / 100.0) * prog.basePointsPer100 * multiplier;
  return raw > prog.monthlyPointsCap;
}

export function makeCard({ name, forexMarkupPct = 3.5, programme = null, transferPartners = [] }) {
  return { name, forexMarkupPct, programme, transferPartners, isZeroForex: forexMarkupPct <= 0.0 };
}

function cardRewardValueInr(card, fareInr, viaPortal = true) {
  return card.programme ? progValueInr(card.programme, fareInr, viaPortal) : 0.0;
}

export function makeTraveller({
  cards = [], loyaltyProgrammes = [], availablePoints = 0, isStudent = false,
  isSenior = false, isArmedForces = false, indianPassport = true,
} = {}) {
  return { cards, loyaltyProgrammes, availablePoints, isStudent, isSenior, isArmedForces, indianPassport };
}

function bestForexCard(cards) {
  return cards.reduce((best, c) => (best === null || c.forexMarkupPct < best.forexMarkupPct ? c : best), null);
}

function bestRewardCard(cards, fareInr) {
  if (cards.length === 0) return null;
  return cards.reduce((best, c) =>
    cardRewardValueInr(c, fareInr) > cardRewardValueInr(best, fareInr) ? c : best
  );
}

export function makeTrip({
  origin, destination, depart, ret = null, cabin = "economy", pax = 1,
  flexibility = makeFlexibility(), observedFare = null, baselineFare = null, multiCity = false,
}) {
  if (pax < 1) throw new Error("pax must be at least 1");
  if (ret !== null && ret < depart) throw new Error("return date cannot precede departure");
  const referenceFare = observedFare !== null ? observedFare : baselineFare;
  return {
    origin, destination, depart, ret, cabin, pax, flexibility, observedFare, baselineFare, multiCity,
    isRoundTrip: ret !== null,
    referenceFare,
    daysOut(today) { return daysBetween(depart, today); },
  };
}

function makeTactic({
  key, title, detail, expectedSavingPct, hitProbability, channel = null,
  risks = [], exclusivityGroup = null,
}) {
  return {
    key, title, detail, expectedSavingPct, hitProbability, channel, risks, exclusivityGroup,
    expectedValuePct: expectedSavingPct * hitProbability,
  };
}

export function tacticSaving(tactic, fare) {
  if (fare == null) return null;
  return estimateAround(fare * tactic.expectedValuePct);
}

function makePaymentPlan(channel, {
  cashOffInr = 0.0, pointsValueInr = 0.0, optionValueInr = 0.0, forfeitedOptionInr = 0.0,
} = {}) {
  return { channel, steps: [], notes: [], cashOffInr, pointsValueInr, optionValueInr, forfeitedOptionInr };
}

export function comparableTotal(plan, pointsWeight = 1.0, optionWeight = 1.0) {
  return plan.cashOffInr + plan.pointsValueInr * pointsWeight
    + (plan.optionValueInr - plan.forfeitedOptionInr) * optionWeight;
}

// ===========================================================================
// airports.js + routing.js -- mirrors traveler/airports.py, traveler/routing.py
// ===========================================================================

const REGISTRY = new Map();
for (const line of AIRPORTS_RAW.trim().split("\n")) {
  const [iata, country, size, city] = line.split("|");
  REGISTRY.set(iata, { iata, country, size, city });
}

export function airportLookup(code) {
  return REGISTRY.get(code.trim().toUpperCase()) || null;
}

export function countryOf(code) {
  const n = code.trim().toUpperCase();
  const a = REGISTRY.get(n);
  if (a) return a.country;
  if (INDIAN_AIRPORTS.has(n)) return "IN";
  return AIRPORT_COUNTRY[n] || null;
}

export function isIndian(code) {
  return countryOf(code) === "IN";
}

function isLargeAirport(code) {
  const n = code.trim().toUpperCase();
  const a = REGISTRY.get(n);
  if (a) return a.size === "L";
  return MAINSTREAM_AIRPORTS.has(n);
}

export function isMainstream(code) {
  const n = code.trim().toUpperCase();
  return isLargeAirport(n) || MAINSTREAM_AIRPORTS.has(n);
}

export function classify(trip) {
  const origin = trip.origin.trim().toUpperCase();
  const dest = trip.destination.trim().toUpperCase();
  const originIn = isIndian(origin);
  const destIn = isIndian(dest);
  if (originIn && destIn) return "domestic_india";
  if (!originIn && !destIn) return "foreign_domestic";
  const foreign = originIn ? dest : origin;
  const country = countryOf(foreign);
  if (country && SHORT_HAUL_COUNTRIES.has(country)) return "short_haul_intl";
  return "long_haul_intl";
}

export function isUdanCandidate(trip, routeClass) {
  if (routeClass !== "domestic_india") return false;
  return !(isMainstream(trip.origin) && isMainstream(trip.destination));
}

export function supportsOpenJaw(trip) {
  return trip.isRoundTrip || trip.multiCity;
}

export function supportsSplitTicket(trip, routeClass) {
  if (routeClass === "domestic_india" || routeClass === "foreign_domestic") return false;
  return trip.flexibility.acceptsSelfTransfer;
}

export function supportsPositioning(trip, routeClass) {
  if (routeClass !== "long_haul_intl") return false;
  return trip.flexibility.originFlex;
}

export function minSelfTransferBufferHours(cabinPremium = false) {
  return cabinPremium ? 5 : 6;
}

// ===========================================================================
// market.js -- mirrors traveler/market.py. FittedMarketModel is out of
// scope (calibration package is CLI/Python-only per the plan); only
// DefaultMarketModel is ported.
// ===========================================================================

export const MODEL_PROVENANCE =
  "hand-authored priors (uncalibrated) -- run calibration.fit against observed fares to replace";

export function priceMultiplier(daysOut, routeClass) {
  const [lo, hi] = BOOKING_WINDOWS[routeClass];
  const cliff = LAST_MINUTE_CLIFF_DAYS[routeClass];
  if (daysOut < 0) return Infinity;
  if (daysOut <= cliff) {
    const ratio = daysOut / Math.max(cliff, 1);
    return 2.0 - 0.75 * ratio;
  }
  if (daysOut < lo) {
    const span = Math.max(lo - cliff, 1);
    const ratio = (daysOut - cliff) / span;
    return 1.25 - 0.25 * ratio;
  }
  if (daysOut <= hi) return 1.0;
  const excess = daysOut - hi;
  return Math.min(1.0 + 0.0015 * excess, 1.20);
}

export function volatilityPct(daysOut, routeClass) {
  if (daysOut <= 0) return 0.0;
  const cliff = LAST_MINUTE_CLIFF_DAYS[routeClass];
  const near = 12.0, far = 2.0;
  const halfLife = Math.max(cliff, 1) * 1.5;
  const decayed = far + (near - far) * Math.exp(-daysOut / halfLife);
  return roundTo(decayed, 2);
}

// ===========================================================================
// seasonality.js -- mirrors traveler/seasonality.py
// ===========================================================================

function inWindow(day, start, end) {
  const here = [day.getUTCMonth() + 1, day.getUTCDate()];
  const cmp = (a, b) => (a[0] !== b[0] ? a[0] - b[0] : a[1] - b[1]);
  if (cmp(start, end) <= 0) {
    return cmp(start, here) <= 0 && cmp(here, end) <= 0;
  }
  return cmp(here, start) >= 0 || cmp(here, end) <= 0;
}

export function peaksFor(depart) {
  const hits = [];
  for (const [start, end, mult, label] of RECURRING_PEAKS) {
    if (inWindow(depart, start, end)) hits.push([mult, label]);
  }
  const year = depart.getUTCFullYear();
  for (const [start, end, mult, label] of (FESTIVAL_PEAKS[year] || [])) {
    if (inWindow(depart, start, end)) hits.push([mult, label]);
  }
  return hits;
}

export function seasonMultiplier(depart, routeClass) {
  const hits = peaksFor(depart);
  const sensitivity = SEASON_SENSITIVITY[routeClass];

  if (hits.length === 0) {
    let note = null;
    if (depart.getUTCFullYear() > LAST_CALENDARED_YEAR) {
      note = `No festival calendar for ${depart.getUTCFullYear()} (calendared through ` +
        `${LAST_CALENDARED_YEAR}); recurring peaks only. Extend FESTIVAL_PEAKS in knowledge.py.`;
    }
    return [1.0, note];
  }

  let rawMult = -Infinity, label = null;
  for (const [m, l] of hits) if (m > rawMult) { rawMult = m; label = l; }
  const effective = 1.0 + (rawMult - 1.0) * sensitivity;

  const labels = [...new Set(hits.map((h) => h[1]))].sort().join(", ");
  const note = `Departure falls in a demand peak (${labels}): expect roughly ` +
    `${Math.round((effective - 1.0) * 100)}% above off-peak pricing on this route class. ` +
    `Book earlier than the normal window and expect the trough never to arrive.`;
  return [roundTo(effective, 3), note];
}

// ===========================================================================
// timing.js -- mirrors traveler/timing.py
// ===========================================================================

const REGIME_BIAS = { falling: -7, stable: 0, rising: 10 };

export function windowFor(routeClass, regime) {
  const [lo, hi] = BOOKING_WINDOWS[routeClass];
  const bias = REGIME_BIAS[regime];
  return [lo + bias, hi + bias];
}

export function urgency(daysOut, routeClass, regime, seasonMult = 1.0) {
  const [lo, hi] = windowFor(routeClass, regime);
  const cliff = LAST_MINUTE_CLIFF_DAYS[routeClass];
  const peak = seasonMult >= 1.10;

  if (daysOut < 0) return ["book_now", "Departure date is in the past."];

  if (daysOut <= cliff) {
    return ["book_now",
      `Inside the ${cliff}-day last-minute cliff. Fares rise steeply from here; ` +
      `every day of waiting costs more than it can save.`];
  }

  if (daysOut < lo) {
    return ["book_now",
      `Past the trough (${lo}-${hi} days out) and approaching the cliff at ${cliff} days. ` +
      `Book at the first acceptable price.`];
  }

  if (daysOut <= hi) {
    let note = `Inside the optimal window (${lo}-${hi} days out).`;
    if (regime === "rising") {
      note += " Market regime is RISING, so bias toward the early half of the window and lock rather than wait for a dip.";
    }
    if (peak) {
      return ["book_now", note +
        " Peak-season departure: the usual trough is unlikely to materialise, so treat this as book-now rather than watch."];
    }
    return ["hold_and_watch", note];
  }

  if (daysOut <= hi + 60) {
    let note = `Earlier than the ${lo}-${hi} day window. Set alerts now; expect better pricing as inventory is optimised.`;
    if (peak) {
      return ["hold_and_watch", note +
        " Peak-season departure, so start watching now rather than waiting for the window to open."];
    }
    return ["wait", note];
  }

  return ["too_early",
    `Very early (${daysOut} days out). Schedules may not be finalised. Track, but do not expect the trough yet.`];
}

export function triggerPrice(baselineFare, daysOut, routeClass, seasonMult = 1.0) {
  if (baselineFare == null) return null;
  return roundTo(baselineFare * priceMultiplier(daysOut, routeClass) * seasonMult, 2);
}

// ===========================================================================
// options.js -- mirrors traveler/options.py
// ===========================================================================

const UPSIDE_HAIRCUT = 0.4;
const LOOKIN_CAPTURE = 0.25;

function upsideOnlyMove(fare, volPct) {
  return fare * (volPct / 100.0) * UPSIDE_HAIRCUT;
}

export function fareHold(trip, routeClass, daysOut, decisionProbability = 0.8) {
  const domestic = routeClass === "domestic_india";
  const product = FARE_HOLD_PRODUCTS[domestic ? "indigo_6e_domestic" : "indigo_6e_international"];
  const cost = product.cost_inr * trip.pax;
  const label = product.label;

  const fare = trip.referenceFare;
  if (fare == null) {
    return {
      instrument: label, costInr: cost, expectedValue: makeEstimate(0, 0, 0), recommended: true,
      rationale: `No fare supplied, so the expected value cannot be computed. At ${fmt(cost)} INR ` +
        `the downside is negligible -- buy the hold if you are still deciding.`,
    };
  }

  const vol = volatilityPct(daysOut, routeClass);
  const ev = estimateAround(upsideOnlyMove(fare, vol) * decisionProbability);
  const recommended = ev.mid > cost;
  const ratio = cost ? ev.mid / cost : Infinity;
  let rationale = `Fare ${fmt(fare)} INR, expected ${vol.toFixed(1)}% movement over the hold window, ` +
    `${(decisionProbability * 100).toFixed(0)}% chance of proceeding. Expected value ${estStr(ev)} INR ` +
    `against ${fmt(cost)} INR cost (${ratio.toFixed(1)}x at mid).`;
  if (!recommended) rationale += " Cheap enough to buy anyway if your plans are unsettled.";
  return { instrument: label, costInr: cost, expectedValue: ev, recommended, rationale };
}

export function dgcaLookin(trip, routeClass, daysOut) {
  const international = routeClass !== "domestic_india";
  const minDays = international ? DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL : DGCA_LOOKIN_MIN_DAYS_DOMESTIC;
  if (daysOut < minDays) return null;

  const fare = trip.referenceFare;
  const vol = volatilityPct(daysOut, routeClass);
  const ev = fare == null ? makeEstimate(0, 0, 0) : estimateAround(fare * (vol / 100.0) * LOOKIN_CAPTURE);

  let rationale = `Booking direct preserves a free ${DGCA_LOOKIN_HOURS}-hour window to cancel or amend ` +
    `(departure is ${daysOut} days out, minimum ${minDays}). `;
  rationale += fare != null ? `Worth roughly ${estStr(ev)} INR as a free look.` : "Free, so always worth keeping.";
  rationale += " Airline-direct bookings only -- OTA bookings do not qualify.";

  return {
    instrument: `DGCA ${DGCA_LOOKIN_HOURS}h look-in window`, costInr: 0.0,
    expectedValue: ev, recommended: true, rationale,
  };
}

export function awardPlaceholder(trip, hasPoints) {
  if (!hasPoints) return null;
  const fare = trip.referenceFare;
  const ev = fare == null ? makeEstimate(0, 0, 0) : estimateAround(fare * 0.10);
  return {
    instrument: "Award seat as free placeholder", costInr: 0.0, expectedValue: ev, recommended: true,
    rationale: "Major programmes allow free cancellation and mile redeposit before departure. Hold a " +
      "confirmed award seat, keep hunting cash fares, and cancel free if cash wins. Check the " +
      "programme's redeposit rule first -- basic awards and non-US-origin itineraries are tighter.",
  };
}

export function couponVersusLookin(couponInr, lookin) {
  if (lookin == null) {
    return `No DGCA window applies to this booking, so the ${fmt(couponInr)} INR OTA discount is free money. Take it.`;
  }
  if (couponInr > lookin.expectedValue.mid) {
    return `Take the OTA discount: ${fmt(couponInr)} INR beats the ${estStr(lookin.expectedValue)} INR ` +
      `option value of the DGCA window. Valid only because your plans are firm.`;
  }
  return `Book direct and keep the DGCA window. Its option value (${estStr(lookin.expectedValue)} INR) ` +
    `exceeds the ${fmt(couponInr)} INR OTA discount, and the window also protects you against your ` +
    `own plans changing.`;
}

// ===========================================================================
// payments.js -- mirrors traveler/payments.py
// ===========================================================================

const OTA_USES_PORTAL = false;

function applicableOffers(bookingDay) {
  return OTA_OFFERS.filter((o) => o.days === null || o.days.includes(bookingDay));
}

function offerValue(offer, fare) {
  return Math.min(fare * offer.pct / 100.0, offer.cap_inr);
}

export function effectivePct(offer, fare) {
  if (fare <= 0) return 0.0;
  return 100.0 * offerValue(offer, fare) / fare;
}

export function bestOtaOffer(fare, bookingDay) {
  const live = applicableOffers(bookingDay);
  if (live.length === 0) return null;
  let best = null, bestVal = -Infinity;
  for (const o of live) {
    const v = offerValue(o, fare);
    if (v > bestVal) { bestVal = v; best = o; }
  }
  return [best, bestVal];
}

export function emiTrueCost(fare, tenureMonths = 3, nominalRatePct = 14.0) {
  const notionalInterest = fare * (nominalRatePct / 100.0) * (tenureMonths / 12.0);
  const gst = notionalInterest * (EMI_GST_PCT / 100.0);
  return gst + EMI_TYPICAL_PROCESSING_FEE_INR;
}

export function forexCost(fare, traveller, foreignPos) {
  if (!foreignPos) return [0.0, "Domestic point of sale: no forex markup applies."];
  const card = bestForexCard(traveller.cards);
  const markup = card ? card.forexMarkupPct : DEFAULT_FOREX_MARKUP_PCT;
  const cost = fare * markup / 100.0;
  let note;
  if (card && card.isZeroForex) {
    note = `Use ${card.name} (zero forex markup) -- saves the usual 2-3.5%.`;
  } else if (card) {
    note = `Best available card is ${card.name} at ${markup.toFixed(1)}% markup (${fmt(cost)} INR). ` +
      `A zero-forex card would save this entirely.`;
  } else {
    note = `No card supplied; assuming ${markup.toFixed(1)}% markup (${fmt(cost)} INR).`;
  }
  return [cost, note];
}

function rewardNote(traveller, fare, viaPortal) {
  const card = bestRewardCard(traveller.cards, fare);
  if (!card || !card.programme) return [0.0, null];
  const prog = card.programme;
  const points = pointsEarned(prog, fare, viaPortal);
  const value = progValueInr(prog, fare, viaPortal);
  const where = viaPortal ? "issuer portal" : "direct/OTA rate";
  let note = `Pay with ${card.name}: ${fmt(points)} points at ${where}, worth about ${fmt(value)} INR ` +
    `after a ${(prog.realizationRate * 100).toFixed(0)}% realisation haircut at ` +
    `${prog.pointValueInr.toFixed(2)} INR/point.`;
  if (capBinds(prog, fare, viaPortal)) {
    note += ` The ${fmt(prog.monthlyPointsCap)} point monthly cap binds on this booking -- earning above it is forfeited.`;
  }
  return [value, note];
}

export function optimisePayment(trip, traveller, routeClass, bookingDate, lookinValueInr = 0.0, foreignPos = false) {
  const fare = trip.referenceFare;
  const day = pythonWeekday(bookingDate);

  if (fare == null) {
    const plan = makePaymentPlan("airline_direct");
    plan.steps.push("No fare supplied -- defaulting to airline-direct, which preserves the DGCA look-in window and is the safe default under NDC.");
    plan.notes.push("Re-run with --fare to get the payment stack costed out.");
    return plan;
  }

  const [directPoints, directNote] = rewardNote(traveller, fare, true);
  const directPlan = makePaymentPlan("airline_direct", { pointsValueInr: directPoints, optionValueInr: lookinValueInr });
  directPlan.steps.push("Book on the airline's own site.");
  if (directNote) directPlan.steps.push(directNote);

  const ota = bestOtaOffer(fare, day);
  let otaPlan = null;
  if (ota) {
    const [offer, cash] = ota;
    const [otaPoints, otaNote] = rewardNote(traveller, fare, OTA_USES_PORTAL);
    otaPlan = makePaymentPlan("ota", { cashOffInr: cash, pointsValueInr: otaPoints, forfeitedOptionInr: lookinValueInr });
    otaPlan.steps.push(
      `Book on ${offer.platform} with ${offer.issuer}: ${offer.pct}% capped at ${fmt(offer.cap_inr)} INR ` +
      `-> ${fmt(cash)} INR off (effective ${effectivePct(offer, fare).toFixed(1)}%).`
    );
    if (otaNote) otaPlan.steps.push(otaNote);
    if (offer.days !== null) {
      otaPlan.notes.push("This offer is day-of-week gated -- confirm it is live before booking. Offer tables rotate constantly.");
    }
    otaPlan.notes.push(`This forfeits the DGCA look-in window (worth ~${fmt(lookinValueInr)} INR). Only correct because your plans are firm.`);
  }

  let plan;
  if (otaPlan && comparableTotal(otaPlan) > comparableTotal(directPlan)) {
    plan = otaPlan;
    plan.notes.push(`Chosen over direct on comparable total: ${fmt(comparableTotal(otaPlan))} vs ${fmt(comparableTotal(directPlan))} INR.`);
  } else {
    plan = directPlan;
    if (otaPlan) {
      plan.notes.push(
        `Best OTA alternative delivered ${fmt(otaPlan.cashOffInr)} INR cash, which does not beat direct ` +
        `once portal points and the forfeited DGCA window are priced in (${fmt(comparableTotal(otaPlan))} ` +
        `vs ${fmt(comparableTotal(directPlan))} INR).`
      );
    }
  }

  const [, fxNote] = forexCost(fare, traveller, foreignPos);
  plan.notes.push(fxNote);
  if (foreignPos) {
    plan.notes.push(
      `Decline dynamic currency conversion -- accepting INR billing abroad typically costs a further ` +
      `~${DCC_PENALTY_PCT.toFixed(0)}% (${fmt(fare * DCC_PENALTY_PCT / 100.0)} INR).`
    );
  }
  plan.notes.push(
    `'No-cost' EMI would add roughly ${fmt(emiTrueCost(fare))} INR in GST on notional interest plus ` +
    `processing fee, and blocks other coupons. Foreclosure costs a further ${EMI_FORECLOSURE_PCT.toFixed(0)}% ` +
    `+ GST. Decline unless the cash-flow benefit is worth that.`
  );
  plan.notes.push(
    "Check whether a discounted brand voucher exists on SmartBuy/Gyftr: voucher discount stacks with " +
    "the platform's own sale, and the voucher purchase still earns reward points."
  );
  return plan;
}

// ===========================================================================
// tactics.js -- mirrors traveler/tactics.py
// ===========================================================================

function p(key) { return TACTIC_PRIORS[key]; }

export function buildTactics(trip, traveller, routeClass) {
  const flex = trip.flexibility;
  const out = [];

  if (routeClass === "long_haul_intl") {
    let prob = cabinIsPremium(trip.cabin) ? 0.55 : 0.35;
    if (flex.hardDates) prob += 0.15;
    out.push(makeTactic({
      key: "consolidator", title: "Consolidator / net fares",
      detail: "Contract fares on hard-to-fill long-haul inventory, discounted 30-60% and contractually " +
        "barred from online display. Reachable only through an agent with GDS access. This is the one " +
        "lever that works when you have no flexibility at all.",
      expectedSavingPct: p("consolidator"), hitProbability: Math.min(prob, 0.8),
      channel: "consolidator", exclusivityGroup: "channel",
      risks: [
        "Verify a 13-digit e-ticket number, not just a PNR.",
        "Pay by credit card; never bank transfer.",
        "Expect restrictive change/refund rules.",
      ],
    }));
  }

  if (traveller.availablePoints > 0 || traveller.loyaltyProgrammes.length > 0) {
    const prob = cabinIsPremium(trip.cabin) ? 0.5 : 0.3;
    out.push(makeTactic({
      key: "award_redemption", title: "Award redemption",
      detail: "Price the award alongside cash every time. Prefer programmes that do not pass fuel " +
        "surcharges; with ATF elevated the surcharge can exceed the mileage saving. Use seats.aero or " +
        "point.me to find space, and set alerts -- premium space appears and vanishes within hours.",
      expectedSavingPct: p("award_redemption"), hitProbability: prob,
      channel: "award", exclusivityGroup: "channel",
      risks: ["Check redeposit rules before booking as a placeholder."],
    }));
  }

  if (flex.score > 0.4 && flex.canBookFast) {
    out.push(makeTactic({
      key: "error_fare", title: "Error / mistake fares",
      detail: "Misfiled fares, currency errors and unloaded surcharges. Requires genuine flexibility and " +
        "the ability to commit within hours. Book direct with the airline, pay in full, and do not buy " +
        "non-refundable hotels for 72 hours.",
      expectedSavingPct: p("error_fare"), hitProbability: 0.15 * flex.score,
      channel: "airline_direct", exclusivityGroup: "channel",
      risks: ["The airline may cancel and refund rather than honour it."],
    }));
  }

  if (supportsOpenJaw(trip)) {
    out.push(makeTactic({
      key: "open_jaw", title: "Open-jaw / multi-city pricing",
      detail: "Open-jaws price under round-trip rules, so each half costs about half a round-trip while " +
        "one-ways carry a yield premium. Free to check -- it is a button in Google Flights -- and it " +
        "also removes the backtrack leg. Price it both ways every time.",
      expectedSavingPct: p("open_jaw"), hitProbability: 0.45,
    }));
  }

  if (supportsSplitTicket(trip, routeClass)) {
    out.push(makeTactic({
      key: "split_ticket", title: "Split ticketing / self-transfer",
      detail: `Separate tickets often undercut the through-fare by 25-40%. Allow at least ` +
        `${minSelfTransferBufferHours(cabinIsPremium(trip.cabin))}h between tickets, or an overnight at the gateway.`,
      expectedSavingPct: p("split_ticket"), hitProbability: 0.4, exclusivityGroup: "routing",
      risks: [
        "The onward carrier has no obligation if the feeder is late.",
        "Check transit-visa rules for your passport.",
        "Bags usually must be re-checked.",
      ],
    }));
  }

  if (supportsPositioning(trip, routeClass)) {
    out.push(makeTactic({
      key: "positioning", title: "Positioning flight",
      detail: "Start the long-haul from a cheaper gateway. Metro origins are frequently far cheaper than " +
        "tier-2 even after buying the domestic hop; neighbouring-country origins can beat both.",
      expectedSavingPct: p("positioning"), hitProbability: 0.35, exclusivityGroup: "routing",
      risks: ["Separate tickets -- same buffer rules as split ticketing."],
    }));
  }

  if (isUdanCandidate(trip, routeClass)) {
    out.push(makeTactic({
      key: "udan_cap", title: "UDAN capped fare",
      detail: `Regional routes under the UDAN scheme are fare-capped at about ${fmt(UDAN_FARE_CAP_INR)} INR ` +
        `for a ~1h sector. This is a regulated ceiling, not a sale -- no timing or luck needed. Check ` +
        `whether your sector or a nearby airport is on the operational route list.`,
      expectedSavingPct: p("udan_cap"), hitProbability: 0.2, channel: "airline_direct",
    }));
  }

  if (routeClass === "long_haul_intl" || routeClass === "short_haul_intl") {
    const card = bestForexCard(traveller.cards);
    const zeroFx = Boolean(card && card.isZeroForex);
    out.push(makeTactic({
      key: "point_of_sale", title: "Point-of-sale arbitrage",
      detail: "The same seat prices differently by the market you buy from. " +
        (zeroFx
          ? "You hold a zero-forex card, so the saving passes through intact."
          : "Only worth it with a zero-forex card -- a 3.5% markup eats most of the gain."),
      expectedSavingPct: p("point_of_sale"), hitProbability: zeroFx ? 0.3 : 0.12,
    }));
  }

  for (const [attr, key] of [["isArmedForces", "armed_forces"], ["isStudent", "student"], ["isSenior", "senior"]]) {
    if (traveller[attr]) {
      const meta = CATEGORY_FARES[key];
      out.push(makeTactic({
        key: `category_${key}`, title: meta.label, detail: meta.note,
        expectedSavingPct: meta.typical_pct / 100.0, hitProbability: 0.9, channel: "airline_direct",
      }));
    }
  }

  if (flex.dateFlexDays > 0) {
    out.push(makeTactic({
      key: "off_peak_shift", title: "Shift to an off-peak departure",
      detail: "Tuesday/Wednesday departures run materially below Friday and Sunday on the same sector, " +
        "and the 5-7am and post-9pm slots add more. Note this is about the day you FLY -- the day you BUY is noise.",
      expectedSavingPct: p("off_peak_shift"), hitProbability: Math.min(0.3 + 0.05 * flex.dateFlexDays, 0.75),
    }));
  }

  if (flex.originFlex && routeClass === "domestic_india") {
    out.push(makeTactic({
      key: "tier2_airport", title: "Alternate airport",
      detail: "Secondary airports carry lower charges, which passes into the fare. Weigh against ground transfer cost and time.",
      expectedSavingPct: p("tier2_airport"), hitProbability: 0.3,
    }));
  }

  if (routeClass === "long_haul_intl" && flex.dateFlexDays >= 2) {
    out.push(makeTactic({
      key: "stopover_program", title: "Free stopover programme",
      detail: "Several hub carriers sell free or near-free multi-day stopovers. It does not cut the fare " +
        "much, but it converts one trip into two destinations at near-zero marginal cost.",
      expectedSavingPct: p("stopover_program"), hitProbability: 0.4,
    }));
  }

  return out;
}

export function portfolioEstimate(tactics, fare) {
  if (fare == null || tactics.length === 0) return null;

  const groups = new Map();
  const independent = [];
  for (const t of tactics) {
    if (t.exclusivityGroup == null) { independent.push(t); continue; }
    const best = groups.get(t.exclusivityGroup);
    if (!best || t.expectedValuePct > best.expectedValuePct) groups.set(t.exclusivityGroup, t);
  }

  let remaining = 1.0;
  for (const t of [...groups.values(), ...independent]) remaining *= (1.0 - t.expectedValuePct);

  return estimateAround(fare * (1.0 - remaining));
}

// ===========================================================================
// rights.js -- mirrors traveler/rights.py
// ===========================================================================

export function rightsApplicable(routeClass, channel, daysOut, touchesUs = false) {
  const out = [];
  const international = routeClass !== "domestic_india";
  const minDays = international ? DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL : DGCA_LOOKIN_MIN_DAYS_DOMESTIC;

  if (channel === "airline_direct" && daysOut >= minDays) {
    out.push(
      `DGCA look-in: free cancellation or amendment within ${DGCA_LOOKIN_HOURS}h of booking (departure ` +
      `must be >= ${minDays} days away). Use it as a free price lock while you keep watching.`
    );
  } else if (channel !== "airline_direct") {
    out.push("No DGCA look-in window: it applies to airline-direct bookings only. Agent/OTA cancellation policy governs instead.");
  } else {
    out.push(`Departure is inside ${minDays} days, so the DGCA look-in window does not apply to this booking.`);
  }

  out.push(
    "Schedule change: if the airline significantly retimes or cancels, you may choose free rebooking OR " +
    "a full refund to original payment -- not a credit shell. Do not passively accept a retiming; a " +
    "better flight is often available at no cost."
  );
  out.push(
    `Refund timelines: ${DGCA_REFUND_DAYS_DIRECT} working days for direct card/UPI/net-banking bookings, ` +
    `up to ${DGCA_REFUND_DAYS_AGENT} working days via an agent or OTA.`
  );

  if (touchesUs) {
    out.push(
      `US DOT: 'significant change' is >= ${DOT_SIGNIFICANT_DELAY_DOMESTIC_H}h domestic or >= ` +
      `${DOT_SIGNIFICANT_DELAY_INTERNATIONAL_H}h international. Refunds are automatic to original payment ` +
      `if you decline the rebooking. A separate 24h free-cancellation rule also applies.`
    );
  }

  out.push(
    "Fare transparency: since March 2026 all non-government fees, including convenience fees, must be " +
    "shown in the initial fare quote. Compare all-in totals, not headline fares."
  );
  return out;
}

export function scheduleChangePlaybook() {
  return [
    "Do not click 'accept' on the airline's proposed alternative.",
    "Check whether the change crosses the significant-change threshold.",
    "If it does, ask for the flight you actually want -- free rebooking is your choice, not the airline's suggestion.",
    "If nothing suits, take the full refund to original payment and rebook.",
    "Escalate in writing citing the rule if the first agent refuses.",
  ];
}

// ===========================================================================
// engine.js -- mirrors traveler/engine.py
// ===========================================================================

function touchesUs(trip) {
  return [trip.origin, trip.destination].some((code) => countryOf(code) === "US");
}

export function fareVerdict(trip, trigger) {
  const fare = trip.observedFare;
  if (fare == null || trigger == null) return null;

  const delta = trigger - fare;
  const pct = 100.0 * delta / trigger;

  if (delta >= 0) {
    return `BUY. The fare in hand (${fmt(fare)} INR) is ${pct.toFixed(1)}% below your trigger of ` +
      `${fmt(trigger)} INR. You pre-committed to this price -- book it without further deliberation.`;
  }
  if (pct > -8.0) {
    return `MARGINAL. ${fmt(fare)} INR is ${Math.abs(pct).toFixed(1)}% above your ${fmt(trigger)} INR ` +
      `trigger. Buy a fare hold and give it a few days rather than accepting or walking away now.`;
  }
  return `HOLD. ${fmt(fare)} INR is ${Math.abs(pct).toFixed(1)}% above your ${fmt(trigger)} INR trigger. ` +
    `Keep alerts running and work the ranked tactics below.`;
}

function computeRisks(trip, traveller, routeClass, plan) {
  const out = [];

  if (trip.flexibility.acceptsSelfTransfer) {
    const bufferH = minSelfTransferBufferHours(cabinIsPremium(trip.cabin));
    out.push(`Self-transfer accepted: enforce a ${bufferH}h minimum between separate tickets, or take an overnight at the gateway.`);
  }

  if (traveller.indianPassport && (routeClass === "long_haul_intl" || routeClass === "short_haul_intl")) {
    out.push(
      "Screen every cheap routing for transit-visa requirements on an Indian passport. Generic metasearch " +
      "will happily show itineraries you cannot legally fly."
    );
  }

  if (plan.tactics.some((t) => t.key === "consolidator")) {
    out.push("Consolidator bookings: confirm the 13-digit e-ticket on the airline's own site within 24 hours. A PNR is not a ticket.");
  }

  if (trip.flexibility.hardDates) {
    out.push("Hard dates remove every flexibility-based tactic. Consolidators, fare holds and the DGCA window are your remaining levers.");
  }

  if (trip.referenceFare == null) {
    out.push("No fare data supplied: option values and the payment stack are qualitative only. Re-run with --fare and --baseline for numbers.");
  }

  out.push(`Model provenance: ${MODEL_PROVENANCE}. Rankings are more reliable than the rupee figures until calibration has run.`);
  out.push("Pay by credit card in all cases -- chargeback rights are the backstop for every other risk here.");
  return out;
}

// NOTE ON A DISCLOSED DEVIATION FROM THE PYTHON SOURCE:
//
// Python's `engine.summarise()` calls `plan.trip.days_out()` with NO
// argument, which falls back to `datetime.date.today()` -- the real
// wall-clock date at call time, NOT the `--today` value the CLI may have
// used to compute the rest of the plan. The CLI's plain-text renderer has
// the same bug (`t.days_out()`, no arg, in cli.py's render()). Every other
// figure in the plan (urgency, trigger price, options, tactics) correctly
// uses the injected `today`; only the *displayed* days-out figure does not.
//
// This is a latent bug in the Python CLI, not a design decision worth
// porting. `toSummary` below requires `today` explicitly and computes
// days_out from it, which is the value the Python code evidently intended.
// See PORT_NOTES.md.
export function planTrip(trip, traveller, {
  regime = "rising", today = null, decisionProbability = 0.8, foreignPos = false,
} = {}) {
  today = today || todayUTC();
  const daysOut = trip.daysOut(today);
  const routeClass = classify(trip);
  const [seasonMult, seasonNote] = seasonMultiplier(trip.depart, routeClass);
  const [urgencyLevel, timingNote] = urgency(daysOut, routeClass, regime, seasonMult);

  const plan = {
    trip, routeClass, urgency: urgencyLevel, timingNote,
    triggerPriceInr: triggerPrice(trip.baselineFare, daysOut, routeClass, seasonMult),
    seasonMultiplier: seasonMult, seasonNote,
    fareVerdict: null,
    tactics: [], options: [], payment: null, rights: [], risks: [],
  };
  plan.fareVerdict = fareVerdict(trip, plan.triggerPriceInr);

  plan.tactics = buildTactics(trip, traveller, routeClass);

  const lookin = dgcaLookin(trip, routeClass, daysOut);
  if (lookin) plan.options.push(lookin);
  if (urgencyLevel === "hold_and_watch" || urgencyLevel === "book_now") {
    plan.options.push(fareHold(trip, routeClass, daysOut, decisionProbability));
  }
  const award = awardPlaceholder(trip, traveller.availablePoints > 0 || traveller.loyaltyProgrammes.length > 0);
  if (award) plan.options.push(award);

  plan.payment = optimisePayment(
    trip, traveller, routeClass, today, lookin ? lookin.expectedValue.mid : 0.0, foreignPos
  );

  plan.rights = rightsApplicable(routeClass, plan.payment.channel, daysOut, touchesUs(trip));
  plan.risks = computeRisks(trip, traveller, routeClass, plan);
  return plan;
}

export function headlineTactics(plan) {
  return [...plan.tactics].sort((a, b) => b.expectedValuePct - a.expectedValuePct);
}

export function toSummary(plan, today) {
  const fare = plan.trip.referenceFare;
  const portfolio = portfolioEstimate(plan.tactics, fare);
  const payment = plan.payment;
  return {
    route_class: plan.routeClass,
    urgency: plan.urgency,
    days_out: plan.trip.daysOut(today), // see the deviation note above planTrip
    trigger_price_inr: plan.triggerPriceInr,
    fare_verdict: plan.fareVerdict,
    season_multiplier: plan.seasonMultiplier,
    top_tactics: headlineTactics(plan).slice(0, 5).map((t) => t.key),
    portfolio_saving_inr: portfolio
      ? { low: roundTo(portfolio.low, 2), mid: roundTo(portfolio.mid, 2), high: roundTo(portfolio.high, 2) }
      : null,
    payment_channel: payment ? payment.channel : null,
    payment_cash_off_inr: payment ? payment.cashOffInr : null,
    payment_points_value_inr: payment ? payment.pointsValueInr : null,
    payment_option_value_inr: payment ? payment.optionValueInr - payment.forfeitedOptionInr : null,
  };
}

// ===========================================================================
// Card catalogue -- mirrors the six real cards in traveler/cli.py
// ===========================================================================

const _HDFC_PREMIUM = makeRewardProgramme({
  basePointsPer100: 3.3, pointValueInr: 1.0, realizationRate: 0.45,
  portalMultiplier: 5.0, monthlyPointsCap: HDFC_SMARTBUY_MONTHLY_POINTS_CAP,
});

export const CARD_CATALOGUE = {
  infinia: makeCard({
    name: "HDFC Infinia", forexMarkupPct: 2.0, programme: _HDFC_PREMIUM,
    transferPartners: ["KrisFlyer", "Executive Club", "Miles&Smiles"],
  }),
  "diners-black": makeCard({
    name: "HDFC Diners Club Black", forexMarkupPct: 2.0, programme: _HDFC_PREMIUM,
    transferPartners: ["KrisFlyer", "Executive Club"],
  }),
  scapia: makeCard({
    name: "Federal Bank Scapia", forexMarkupPct: 0.0,
    programme: makeRewardProgramme({ basePointsPer100: 2.0, pointValueInr: 1.0 }),
  }),
  "idfc-wow": makeCard({
    name: "IDFC FIRST WoW", forexMarkupPct: 0.0,
    programme: makeRewardProgramme({ basePointsPer100: 1.0, pointValueInr: 1.0 }),
  }),
  mayura: makeCard({
    name: "IDFC FIRST Mayura", forexMarkupPct: 0.0,
    programme: makeRewardProgramme({ basePointsPer100: 2.5, pointValueInr: 1.0 }),
  }),
  "magnus-burgundy": makeCard({
    name: "Axis Magnus for Burgundy", forexMarkupPct: 2.0,
    programme: makeRewardProgramme({ basePointsPer100: 2.4, pointValueInr: 1.0 }),
    transferPartners: ["KrisFlyer", "Flying Blue"],
  }),
};

export { fmt, roundTo, daysBetween, pythonWeekday };
