"""Reference data snapshot.

Every constant here is a fact about the world that decays. Each block carries
the date it was checked and where it came from, so it can be re-verified
rather than trusted indefinitely. The engine's *logic* lives elsewhere; this
module is deliberately just data.

Snapshot date: 2026-08-12
"""

from __future__ import annotations

SNAPSHOT_DATE = "2026-08-12"

# --------------------------------------------------------------------------
# Airports / geography
# --------------------------------------------------------------------------

#: Non-exhaustive. Enough to classify the routes Indian travellers actually
#: fly; unknown codes fall back to explicit user input.
INDIAN_AIRPORTS: frozenset[str] = frozenset(
    """
    DEL BOM BLR MAA HYD CCU COK AMD PNQ GOI GOX JAI LKO IXC TRV IXM CJB
    VNS PAT BBI GAU IXB IXR NAG IDR BHO RPR SXR IXJ ATQ DED HBX IXE IXZ
    STV RAJ BDQ UDR JDH IXA SHL IMF DIB IXS TIR VTZ VGA MYQ TRZ IXL AGX
    HGI KNU GWL JLR BHU PGH SLV DHM SXV CDP RJA JGB PYG DBR HSS
    """.split()
)

#: Airports with mainstream, full-fare scheduled service. UDAN subsidised
#: sectors connect regional strips, so a route between two of these is never
#: a UDAN candidate -- which is what stopped DEL-JAI being flagged.
MAINSTREAM_AIRPORTS: frozenset[str] = frozenset(
    """
    DEL BOM BLR MAA HYD CCU COK AMD PNQ GOI GOX JAI LKO IXC TRV NAG IDR
    BBI GAU PAT VNS SXR ATQ RPR BHO IXR JDH VTZ TIR CJB IXM IXE IXB IXJ
    IXA IMF SHL DIB TRZ UDR BDQ RAJ STV IXZ IXL DED HBX
    """.split()
)

#: Countries reachable as "short haul" from India (< ~5h typical block time).
SHORT_HAUL_COUNTRIES: frozenset[str] = frozenset(
    {"AE", "OM", "QA", "BH", "KW", "SA", "LK", "NP", "BD", "BT", "MV", "MM",
     "TH", "MY", "SG", "PK", "AF", "UZ", "KZ"}
)

#: Airport -> ISO country, for the subset used in classification.
AIRPORT_COUNTRY: dict[str, str] = {
    # Gulf
    "DXB": "AE", "AUH": "AE", "SHJ": "AE", "DOH": "QA", "MCT": "OM",
    "BAH": "BH", "KWI": "KW", "RUH": "SA", "JED": "SA",
    # South / SE Asia
    "CMB": "LK", "KTM": "NP", "DAC": "BD", "PBH": "BT", "MLE": "MV",
    "BKK": "TH", "DMK": "TH", "HKT": "TH", "KUL": "MY", "SIN": "SG",
    "RGN": "MM", "KHI": "PK", "LHE": "PK", "TAS": "UZ",
    # Long haul (sample)
    "LHR": "GB", "LGW": "GB", "CDG": "FR", "FRA": "DE", "MUC": "DE",
    "AMS": "NL", "ZRH": "CH", "FCO": "IT", "MAD": "ES", "IST": "TR",
    "JFK": "US", "EWR": "US", "SFO": "US", "ORD": "US", "IAD": "US",
    "LAX": "US", "MIA": "US", "BOS": "US", "SEA": "US", "ATL": "US",
    "DFW": "US", "PHX": "US", "DEN": "US", "LAS": "US", "MCO": "US",
    "PHL": "US", "CLT": "US", "DTW": "US", "MSP": "US", "SLC": "US",
    "AUS": "US", "IAH": "US", "SAN": "US", "TPA": "US", "BWI": "US",
    "DCA": "US", "RDU": "US", "PDX": "US", "STL": "US", "MCI": "US",
    "YYZ": "CA", "YVR": "CA", "YUL": "CA", "SYD": "AU", "MEL": "AU",
    "BNE": "AU", "PER": "AU", "AKL": "NZ",
    "NRT": "JP", "HND": "JP", "ICN": "KR", "HKG": "HK", "PEK": "CN",
    "PVG": "CN", "JNB": "ZA", "NBO": "KE", "CAI": "EG",
}

# --------------------------------------------------------------------------
# Timing model
# --------------------------------------------------------------------------
# Neutral booking windows in days-before-departure, as [trough_start,
# trough_end]. Sources: Going / Forbes Advisor / HappyFares 2026 syntheses;
# note published studies disagree, which is itself the finding -- these are
# centres of mass, not precision.

BOOKING_WINDOWS: dict[str, tuple[int, int]] = {
    "domestic_india": (21, 56),
    "short_haul_intl": (42, 70),
    "long_haul_intl": (60, 150),
    "foreign_domestic": (21, 60),
}

#: Fare multiplier applied inside the last-minute cliff, by route class.
LAST_MINUTE_CLIFF_DAYS: dict[str, int] = {
    "domestic_india": 14,
    "short_haul_intl": 21,
    "long_haul_intl": 28,
    "foreign_domestic": 14,
}

# --------------------------------------------------------------------------
# Indian regulatory levers (DGCA)
# --------------------------------------------------------------------------

#: Free cancellation / amendment window after booking. Effective 2026-03-26.
#: Airline-direct bookings only; OTA bookings fall under the agent's policy.
DGCA_LOOKIN_HOURS = 48
DGCA_LOOKIN_MIN_DAYS_DOMESTIC = 7
DGCA_LOOKIN_MIN_DAYS_INTERNATIONAL = 15

#: Refund timelines after an airline-initiated cancellation, in working days.
DGCA_REFUND_DAYS_DIRECT = 7
DGCA_REFUND_DAYS_AGENT = 14

#: US DOT "significant change" thresholds, in hours.
DOT_SIGNIFICANT_DELAY_DOMESTIC_H = 3
DOT_SIGNIFICANT_DELAY_INTERNATIONAL_H = 6

#: UDAN regional connectivity fare cap for a ~1h / ~500km sector.
UDAN_FARE_CAP_INR = 2500
UDAN_SECTOR_MAX_MINUTES = 60

# --------------------------------------------------------------------------
# Fare-hold products (buy time on a price)
# --------------------------------------------------------------------------

FARE_HOLD_PRODUCTS: dict[str, dict[str, float | int | str]] = {
    "indigo_6e_domestic": {
        "label": "IndiGo 6E Fare Hold (domestic)",
        "cost_inr": 99,
        "hold_hours": 72,
    },
    "indigo_6e_international": {
        "label": "IndiGo 6E Fare Hold (international)",
        "cost_inr": 199,
        "hold_hours": 72,
    },
}

# --------------------------------------------------------------------------
# Award programmes
# --------------------------------------------------------------------------

#: Programmes that do NOT pass carrier fuel surcharges on award tickets.
#: Materially important while ATF is elevated.
SURCHARGE_FREE_PROGRAMMES: frozenset[str] = frozenset(
    {"KrisFlyer", "MileagePlus", "Aeroplan", "Miles&Smiles", "Atmos"}
)

#: Programmes that pass surcharges in full (can exceed $700 RT in business).
SURCHARGE_HEAVY_PROGRAMMES: frozenset[str] = frozenset(
    {"Executive Club", "Flying Club", "Miles & More", "Flying Blue", "SKYPASS"}
)

# --------------------------------------------------------------------------
# Payment layer
# --------------------------------------------------------------------------

#: Representative OTA bank offers. Percentages and caps rotate constantly --
#: this is the SHAPE of the market, to be re-checked before every booking.
OTA_OFFERS: list[dict[str, object]] = [
    {"platform": "Cleartrip", "issuer": "Bank of Baroda", "pct": 25.0,
     "cap_inr": 3000, "days": None},
    {"platform": "Goibibo", "issuer": "Bank of Baroda", "pct": 15.0,
     "cap_inr": 2000, "days": (4, 5)},          # Fri, Sat
    {"platform": "EaseMyTrip", "issuer": "Bank of Baroda", "pct": 15.0,
     "cap_inr": 2000, "days": (2, 3)},          # Wed, Thu
    {"platform": "EaseMyTrip", "issuer": "HSBC", "pct": 15.0,
     "cap_inr": 2500, "days": None},
    {"platform": "MakeMyTrip", "issuer": "SBI debit", "pct": 10.0,
     "cap_inr": 1500, "days": (1, 3, 4)},       # Tue, Thu, Fri
    {"platform": "MakeMyTrip", "issuer": "Visa Signature", "pct": 10.0,
     "cap_inr": 1800, "days": None},
]

#: Typical Indian card forex markup when the card is not zero-forex.
DEFAULT_FOREX_MARKUP_PCT = 3.5

#: HDFC SmartBuy monthly accrual ceiling (Feb 2026 rule change raised the
#: redemption cap to 50,000 points / 5 redemptions per month). Without this
#: the engine credits unbounded accelerated points on large bookings.
HDFC_SMARTBUY_MONTHLY_POINTS_CAP = 50_000

#: Fraction of earned points a realistic traveller redeems at the good rate.
#: 1.0 assumes every point hits the best redemption, which nobody achieves.
DEFAULT_POINT_REALIZATION_RATE = 0.6

#: Dynamic currency conversion penalty when accepting INR billing abroad.
DCC_PENALTY_PCT = 5.0

#: No-cost EMI true cost components (GST on notional interest, fees).
EMI_GST_PCT = 18.0
EMI_TYPICAL_PROCESSING_FEE_INR = 199.0
EMI_FORECLOSURE_PCT = 3.0

# --------------------------------------------------------------------------
# Passenger-category fare discounts (airline-direct only)
# --------------------------------------------------------------------------

CATEGORY_FARES: dict[str, dict[str, object]] = {
    "armed_forces": {
        "label": "Armed forces / defence fare",
        "best_pct": 50.0,           # IndiGo, on base fare
        "typical_pct": 25.0,
        "note": "IndiGo up to 50% off base fare; Air India 25-35% on select "
                "international sectors; Akasa 10% on Saver. Airline site only.",
    },
    "student": {
        "label": "Student fare",
        "best_pct": 10.0,
        "typical_pct": 8.0,
        "note": "IndiGo ~10% off base + 10kg extra baggage, ages 12-25. "
                "Also Akasa, Air India Express, SpiceJet. Valid student ID.",
    },
    "senior": {
        "label": "Senior citizen fare",
        "best_pct": 8.0,
        "typical_pct": 6.0,
        "note": "Ages 60+, auto-applied with Aadhaar on airline sites.",
    },
}

# --------------------------------------------------------------------------
# Seasonality
# --------------------------------------------------------------------------
# Departure-date demand effects, distinct from the days-out effect. In the
# Indian market this is plausibly a LARGER signal than booking window, and
# ignoring it was the biggest gap in the first version of this engine.
#
# !! VERIFY AND EXTEND !! Festival dates move year to year on the lunar
# calendar. The windows below are approximate and cover 2026-2028 only.
# Past the last year present, season_multiplier falls back to the recurring
# peaks alone and says so rather than silently returning 1.0.

#: Peaks that recur on the same civil dates every year, as
#: ((start_month, start_day), (end_month, end_day), multiplier, label).
RECURRING_PEAKS: list[tuple[tuple[int, int], tuple[int, int], float, str]] = [
    ((5, 10), (6, 30), 1.30, "summer school holidays"),
    ((12, 18), (1, 5), 1.45, "Christmas / New Year"),
    ((10, 1), (10, 15), 1.10, "early-October shoulder peak"),
]

#: Year-specific festival windows: year -> list of (start, end, mult, label)
#: as (month, day) tuples. Approximate; verify before relying on them.
FESTIVAL_PEAKS: dict[int, list[tuple[tuple[int, int], tuple[int, int], float, str]]] = {
    2026: [
        ((10, 15), (10, 22), 1.25, "Durga Puja / Dussehra"),
        ((11, 3), (11, 13), 1.40, "Diwali"),
        ((3, 17), (3, 23), 1.15, "Eid al-Fitr"),
    ],
    2027: [
        ((10, 5), (10, 12), 1.25, "Durga Puja / Dussehra"),
        ((10, 24), (11, 3), 1.40, "Diwali"),
        ((3, 7), (3, 13), 1.15, "Eid al-Fitr"),
    ],
    2028: [
        ((9, 23), (9, 30), 1.25, "Durga Puja / Dussehra"),
        ((11, 11), (11, 21), 1.40, "Diwali"),
        ((2, 24), (3, 1), 1.15, "Eid al-Fitr"),
    ],
}

LAST_CALENDARED_YEAR = max(FESTIVAL_PEAKS)

#: Peak demand hits domestic India hardest; long-haul is less festival-driven
#: but still moves on school holidays.
SEASON_SENSITIVITY: dict[str, float] = {
    "domestic_india": 1.0,
    "short_haul_intl": 0.7,
    "long_haul_intl": 0.5,
    "foreign_domestic": 0.3,
}

# --------------------------------------------------------------------------
# Tactic priors
# --------------------------------------------------------------------------
# Expected saving fractions when a tactic lands. Ranges compressed to a
# single expected value; deliberately conservative.

TACTIC_PRIORS: dict[str, float] = {
    "consolidator": 0.35,
    "error_fare": 0.45,
    "award_redemption": 0.55,
    "split_ticket": 0.28,
    "positioning": 0.20,
    "open_jaw": 0.12,
    "point_of_sale": 0.12,
    "udan_cap": 0.30,
    "category_fare": 0.15,
    "off_peak_shift": 0.15,
    "tier2_airport": 0.10,
    "stopover_program": 0.08,
}
