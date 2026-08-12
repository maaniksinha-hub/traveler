"""traveler -- a decision engine for booking flights cheaply from and to India.

This is decision support, not a booking bot. It holds no airline credentials
and makes no bookings; it takes a trip description and emits a ranked,
costed strategy that a human executes.
"""

from .engine import plan_trip, summarise
from .models import (
    Cabin,
    Card,
    Channel,
    Flexibility,
    MarketRegime,
    Plan,
    RouteClass,
    Tactic,
    Traveller,
    Trip,
    Urgency,
)

__version__ = "0.1.0"

__all__ = [
    "plan_trip",
    "summarise",
    "Cabin",
    "Card",
    "Channel",
    "Flexibility",
    "MarketRegime",
    "Plan",
    "RouteClass",
    "Tactic",
    "Traveller",
    "Trip",
    "Urgency",
]
