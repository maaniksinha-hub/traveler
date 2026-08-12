"""Core domain types for the fare strategy engine.

Everything the engine reasons about is expressed here: what the trip is, how
flexible the traveller is, what they can pay with, and what the engine emits.
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
class Card:
    """A payment instrument, described by the properties that affect fares."""

    name: str
    forex_markup_pct: float = 3.5
    #: Reward-points earn rate on travel spend, expressed as % value back.
    travel_reward_pct: float = 0.5
    #: Multiplier when booking via the issuer's portal (e.g. HDFC SmartBuy).
    portal_multiplier: float = 1.0
    #: Loyalty programmes this card can transfer points into.
    transfer_partners: tuple[str, ...] = ()

    @property
    def is_zero_forex(self) -> bool:
        return self.forex_markup_pct <= 0.0


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

    @property
    def best_portal_card(self) -> Card | None:
        return max(self.cards, key=lambda c: c.portal_multiplier, default=None)


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
    #: A fare the traveller has actually seen, in INR, for the whole party.
    observed_fare: float | None = None
    #: What this route normally costs, if known. Enables absolute targets.
    baseline_fare: float | None = None
    #: True when the itinerary visits 3+ cities or arrival/departure differ.
    multi_city: bool = False

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
        return self.observed_fare / max(self.pax, 1)


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

    @property
    def expected_value_pct(self) -> float:
        return self.expected_saving_pct * self.hit_probability

    def saving_inr(self, fare: float | None) -> float | None:
        if fare is None:
            return None
        return fare * self.expected_value_pct


@dataclass
class OptionAdvice:
    """Output of the optionality model -- whether to buy time."""

    instrument: str
    cost_inr: float
    expected_value_inr: float
    recommended: bool
    rationale: str

    @property
    def net_inr(self) -> float:
        return self.expected_value_inr - self.cost_inr


@dataclass
class PaymentPlan:
    """Result of the payment-stack optimiser."""

    channel: Channel
    steps: list[str] = field(default_factory=list)
    gross_discount_inr: float = 0.0
    forfeited_value_inr: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def net_discount_inr(self) -> float:
        return self.gross_discount_inr - self.forfeited_value_inr


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
    tactics: list[Tactic] = field(default_factory=list)
    options: list[OptionAdvice] = field(default_factory=list)
    payment: PaymentPlan | None = None
    rights: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def headline_tactics(self) -> list[Tactic]:
        return sorted(self.tactics, key=lambda t: t.expected_value_pct, reverse=True)
