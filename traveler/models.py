"""Core domain types for the fare strategy engine.

Everything the engine reasons about is expressed here: what the trip is, how
flexible the traveller is, what they can pay with, and what the engine emits.

Money units: every monetary field is INR and **per party** unless its name
ends in ``_per_pax``. Fields that are not cash are named for what they are --
``points_value_inr`` is a speculative valuation, ``option_value_inr`` is not
money at all -- and are never summed into a single headline.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum


class Cabin(Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"

    @property
    def is_premium(self) -> bool:
        return self in (Cabin.PREMIUM_ECONOMY, Cabin.BUSINESS, Cabin.FIRST)


class RouteClass(Enum):
    """Routes behave differently enough that every model branches on this."""

    DOMESTIC_INDIA = "domestic_india"
    SHORT_HAUL_INTL = "short_haul_intl"   # Gulf, SE Asia, Sri Lanka, Nepal
    LONG_HAUL_INTL = "long_haul_intl"     # Europe, Americas, Africa, East Asia
    FOREIGN_DOMESTIC = "foreign_domestic"  # neither end in India


class MarketRegime(Enum):
    """Direction of fare drift.

    This is deliberately a parameter rather than a constant. As of the 2026
    ATF shock the Indian market is RISING, which biases every timing decision
    earlier -- but that is an input, not a law, and should be re-set when the
    regime changes.
    """

    FALLING = "falling"
    STABLE = "stable"
    RISING = "rising"

    @property
    def bias_days(self) -> int:
        """How much earlier (+) to book relative to the neutral window."""
        return {MarketRegime.FALLING: -7, MarketRegime.STABLE: 0, MarketRegime.RISING: 10}[self]


class Urgency(Enum):
    BOOK_NOW = "book_now"
    HOLD_AND_WATCH = "hold_and_watch"
    WAIT = "wait"
    TOO_EARLY = "too_early"


class Channel(Enum):
    AIRLINE_DIRECT = "airline_direct"
    OTA = "ota"
    CONSOLIDATOR = "consolidator"
    AWARD = "award"


# --------------------------------------------------------------------------
# Uncertainty
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    """A value with an honest range around it.

    Most constants in this engine are judgment, not measurement. Emitting a
    band instead of a point stops the output from implying precision the
    model cannot support.
    """

    low: float
    mid: float
    high: float

    def __post_init__(self) -> None:
        if not (self.low <= self.mid <= self.high):
            raise ValueError(
                f"Estimate must satisfy low <= mid <= high, got "
                f"({self.low}, {self.mid}, {self.high})"
            )

    @classmethod
    def around(cls, mid: float, spread: float = 0.45) -> "Estimate":
        """Build a band around a point estimate.

        The default +/-45% reflects how soft these priors genuinely are. It
        should shrink once ``calibration.fit`` has real data behind it.
        """
        return cls(mid * (1.0 - spread), mid, mid * (1.0 + spread))

    def scaled(self, factor: float) -> "Estimate":
        if factor < 0:
            raise ValueError("factor must be non-negative")
        return Estimate(self.low * factor, self.mid * factor, self.high * factor)

    def __str__(self) -> str:
        if self.low == self.high:
            return f"{self.mid:,.0f}"
        return f"{self.low:,.0f}-{self.high:,.0f}"


# --------------------------------------------------------------------------
# Traveller and payment instruments
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Flexibility:
    """How much room the traveller actually has. Drives most tactic gating."""

    date_flex_days: int = 0
    destination_flex: bool = False
    #: Can accept a self-transfer / separate-ticket itinerary.
    accepts_self_transfer: bool = False
    #: Can commit to a fare within a few hours (needed for error fares).
    can_book_fast: bool = False
    #: Willing to fly out of a different origin airport.
    origin_flex: bool = False
    #: Trip has a fixed, non-moveable date (wedding, conference, visa appt).
    hard_dates: bool = False

    def __post_init__(self) -> None:
        if self.date_flex_days < 0:
            raise ValueError("date_flex_days cannot be negative")
        if self.hard_dates and self.date_flex_days:
            raise ValueError(
                "hard_dates=True contradicts date_flex_days="
                f"{self.date_flex_days}. Set one or the other -- silently "
                "resolving this hid real modelling errors."
            )

    @property
    def score(self) -> float:
        """0.0 (locked in) .. 1.0 (fully flexible). Used to weight tactics."""
        if self.hard_dates:
            return 0.0
        parts = [
            min(self.date_flex_days, 14) / 14.0,
            1.0 if self.destination_flex else 0.0,
            1.0 if self.accepts_self_transfer else 0.0,
            1.0 if self.can_book_fast else 0.0,
            1.0 if self.origin_flex else 0.0,
        ]
        return sum(parts) / len(parts)


@dataclass(frozen=True)
class RewardProgramme:
    """How a card turns spend into value.

    Split into explicit stages because collapsing them into one "percent back"
    figure is how the engine previously claimed 16.5% uncapped value-back on
    an HDFC Infinia. Each stage is separately inspectable and tunable:

        spend -> points earned -> monthly cap -> rupee value -> realisation
    """

    #: Points earned per 100 INR of spend, before any portal accelerator.
    base_points_per_100: float
    #: Rupee value of one point at a *good* redemption.
    point_value_inr: float = 1.0
    #: Fraction of points actually redeemed at that good rate. Most people
    #: do not hit the best redemption on every point; 1.0 is fantasy.
    realization_rate: float = 0.6
    #: Issuer-portal accelerator (SmartBuy-style), applied to earning.
    portal_multiplier: float = 1.0
    #: Points ceiling per calendar month, if the programme caps accruals.
    monthly_points_cap: int | None = None

    def points_earned(self, fare_inr: float, via_portal: bool = True) -> float:
        """Points accrued on this spend, after the monthly cap."""
        multiplier = self.portal_multiplier if via_portal else 1.0
        raw = (fare_inr / 100.0) * self.base_points_per_100 * multiplier
        if self.monthly_points_cap is not None:
            return min(raw, float(self.monthly_points_cap))
        return raw

    def value_inr(self, fare_inr: float, via_portal: bool = True) -> float:
        """Realistic rupee value of the points earned on this spend."""
        return (
            self.points_earned(fare_inr, via_portal)
            * self.point_value_inr
            * self.realization_rate
        )

    def cap_binds(self, fare_inr: float, via_portal: bool = True) -> bool:
        """Whether the monthly cap actually clipped this booking."""
        if self.monthly_points_cap is None:
            return False
        multiplier = self.portal_multiplier if via_portal else 1.0
        raw = (fare_inr / 100.0) * self.base_points_per_100 * multiplier
        return raw > self.monthly_points_cap


@dataclass(frozen=True)
class Card:
    """A payment instrument, described by the properties that affect fares."""

    name: str
    forex_markup_pct: float = 3.5
    programme: RewardProgramme | None = None
    #: Loyalty programmes this card can transfer points into.
    transfer_partners: tuple[str, ...] = ()

    @property
    def is_zero_forex(self) -> bool:
        return self.forex_markup_pct <= 0.0

    def reward_value_inr(self, fare_inr: float, via_portal: bool = True) -> float:
        if self.programme is None:
            return 0.0
        return self.programme.value_inr(fare_inr, via_portal)


@dataclass(frozen=True)
class Traveller:
    """Traveller attributes that unlock fare buckets or payment levers."""

    cards: tuple[Card, ...] = ()
    loyalty_programmes: tuple[str, ...] = ()
    available_points: int = 0
    is_student: bool = False
    is_senior: bool = False
    is_armed_forces: bool = False
    #: Indian passport -> transit-visa screening matters on cheap routings.
    indian_passport: bool = True

    @property
    def best_forex_card(self) -> Card | None:
        return min(self.cards, key=lambda c: c.forex_markup_pct, default=None)

    def best_reward_card(self, fare_inr: float) -> Card | None:
        """Rank on realised value for *this* fare, not on raw multiplier.

        The cap means the best card is fare-dependent: a big accelerator that
        clips at 50,000 points can lose to a smaller uncapped one.
        """
        if not self.cards:
            return None
        return max(self.cards, key=lambda c: c.reward_value_inr(fare_inr))


# --------------------------------------------------------------------------
# Trip
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Trip:
    """The booking under consideration."""

    origin: str
    destination: str
    depart: _dt.date
    ret: _dt.date | None = None
    cabin: Cabin = Cabin.ECONOMY
    pax: int = 1
    flexibility: Flexibility = field(default_factory=Flexibility)
    #: A fare the traveller has actually seen, INR, **whole party**.
    observed_fare: float | None = None
    #: What this route normally costs at its trough, INR, **whole party**.
    baseline_fare: float | None = None
    #: True when the itinerary visits 3+ cities or arrival/departure differ.
    multi_city: bool = False

    def __post_init__(self) -> None:
        if self.pax < 1:
            raise ValueError("pax must be at least 1")
        if self.ret is not None and self.ret < self.depart:
            raise ValueError("return date cannot precede departure")

    def days_out(self, today: _dt.date | None = None) -> int:
        today = today or _dt.date.today()
        return (self.depart - today).days

    @property
    def is_round_trip(self) -> bool:
        return self.ret is not None

    @property
    def per_pax_fare(self) -> float | None:
        if self.observed_fare is None:
            return None
        return self.observed_fare / self.pax

    @property
    def per_pax_baseline(self) -> float | None:
        if self.baseline_fare is None:
            return None
        return self.baseline_fare / self.pax

    @property
    def reference_fare(self) -> float | None:
        """Whole-party fare to reason about: observed if known, else baseline."""
        return self.observed_fare if self.observed_fare is not None else self.baseline_fare


# --------------------------------------------------------------------------
# Engine output
# --------------------------------------------------------------------------


@dataclass
class Tactic:
    """One recommended lever, scored so the engine can rank them."""

    key: str
    title: str
    detail: str
    #: Expected saving as a fraction of fare, if the tactic lands.
    expected_saving_pct: float
    #: Probability the tactic is actually available for this trip.
    hit_probability: float
    channel: Channel | None = None
    risks: tuple[str, ...] = ()
    #: Tactics sharing a group are mutually exclusive -- one ticket can only
    #: be bought through one channel, or routed one way. None = combinable.
    exclusivity_group: str | None = None

    @property
    def expected_value_pct(self) -> float:
        return self.expected_saving_pct * self.hit_probability

    def saving(self, fare: float | None) -> Estimate | None:
        if fare is None:
            return None
        return Estimate.around(fare * self.expected_value_pct)


@dataclass
class OptionAdvice:
    """Output of the optionality model -- whether to buy time."""

    instrument: str
    cost_inr: float
    expected_value: Estimate
    recommended: bool
    rationale: str

    @property
    def net_mid_inr(self) -> float:
        return self.expected_value.mid - self.cost_inr


@dataclass
class PaymentPlan:
    """Result of the payment-stack optimiser.

    The three value fields are deliberately **not** summed into a headline.
    Cash off is money. Points value is a speculative valuation that depends
    on redeeming well. Option value is not money at all -- it is the worth of
    keeping a choice open. Adding them produces an authoritative-looking
    number in three incompatible units.
    """

    channel: Channel
    steps: list[str] = field(default_factory=list)
    cash_off_inr: float = 0.0
    points_value_inr: float = 0.0
    option_value_inr: float = 0.0
    #: Option value surrendered by choosing this channel (e.g. the DGCA
    #: window, lost when booking via an OTA).
    forfeited_option_inr: float = 0.0
    notes: list[str] = field(default_factory=list)

    def comparable_total(self, points_weight: float = 1.0,
                         option_weight: float = 1.0) -> float:
        """Single number used ONLY to rank channels against each other.

        Weights are explicit and adjustable precisely because the components
        are not interchangeable. Never render this as a saving.
        """
        return (
            self.cash_off_inr
            + self.points_value_inr * points_weight
            + (self.option_value_inr - self.forfeited_option_inr) * option_weight
        )


@dataclass
class Plan:
    """The engine's complete answer for one trip."""

    trip: Trip
    route_class: RouteClass
    urgency: Urgency
    timing_note: str
    trigger_price_inr: float | None
    #: Verdict on the observed fare against the trigger price, when both exist.
    fare_verdict: str | None = None
    season_multiplier: float = 1.0
    season_note: str | None = None
    tactics: list[Tactic] = field(default_factory=list)
    options: list[OptionAdvice] = field(default_factory=list)
    payment: PaymentPlan | None = None
    rights: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def headline_tactics(self) -> list[Tactic]:
        return sorted(self.tactics, key=lambda t: t.expected_value_pct, reverse=True)
