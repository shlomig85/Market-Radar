"""Signal definitions.

A signal is *data*, not code: it names a measurable phenomenon and states which event types
move it and in which direction. Adding a signal means adding a definition, never editing the
engine.

The sign lives here rather than on the event because the same event means different things
to different signals: a capacity expansion is positive for capital-investment intensity and
negative for supply tightness, and both readings are correct.
"""

from __future__ import annotations

from dataclasses import dataclass

from marketradar.domain.enums import EventType, SignalCategory


@dataclass(frozen=True)
class SignalDefinition:
    key: str
    name: str
    category: SignalCategory
    subject_key: str
    description: str
    #: event type -> signed weight. Positive moves the signal up, negative moves it down.
    event_weights: dict[EventType, float]


SIGNAL_DEFINITIONS: tuple[SignalDefinition, ...] = (
    SignalDefinition(
        key="memory_demand",
        name="Memory demand",
        category=SignalCategory.DEMAND,
        subject_key="memory",
        description="Evidence that end demand for memory products is changing.",
        event_weights={
            EventType.DEMAND_ACCELERATION: 1.0,
            EventType.DEMAND_WEAKNESS: -1.0,
            EventType.CONTRACT_AWARD: 0.5,
        },
    ),
    SignalDefinition(
        key="memory_supply_tightness",
        name="Memory supply tightness",
        category=SignalCategory.SUPPLY,
        subject_key="memory",
        description=(
            "Evidence that supply is tight relative to demand. Capacity expansion counts "
            "negatively: added supply loosens the market."
        ),
        event_weights={
            EventType.SUPPLY_CONSTRAINT: 1.0,
            EventType.INVENTORY_DECLINE: 0.8,
            EventType.INVENTORY_BUILD: -0.8,
            EventType.SUPPLY_EXPANSION: -1.0,
            EventType.CAPACITY_EXPANSION: -0.6,
        },
    ),
    SignalDefinition(
        key="memory_pricing",
        name="Memory pricing",
        category=SignalCategory.PRICING,
        subject_key="memory",
        description="Evidence of change in memory contract or spot pricing.",
        event_weights={
            EventType.PRICING_INCREASE: 1.0,
            EventType.PRICING_DECREASE: -1.0,
        },
    ),
    SignalDefinition(
        key="memory_capital_investment",
        name="Memory capital investment",
        category=SignalCategory.CAPEX,
        subject_key="memory",
        description="Capital being committed to memory production capacity.",
        event_weights={
            EventType.CAPEX_INCREASE: 1.0,
            EventType.CAPEX_DECREASE: -1.0,
            EventType.CAPACITY_EXPANSION: 0.6,
        },
    ),
    SignalDefinition(
        key="ai_infrastructure_investment",
        name="AI infrastructure investment",
        category=SignalCategory.CAPEX,
        subject_key="ai_infrastructure",
        description="Capital and demand flowing into AI compute infrastructure.",
        event_weights={
            EventType.CAPEX_INCREASE: 1.0,
            EventType.DEMAND_ACCELERATION: 0.7,
            EventType.CAPEX_DECREASE: -1.0,
        },
    ),
    SignalDefinition(
        key="semiconductor_equipment_demand",
        name="Semiconductor equipment demand",
        category=SignalCategory.DEMAND,
        subject_key="semiconductor_equipment",
        description="Order intake and lead times at memory-equipment suppliers.",
        event_weights={
            EventType.DEMAND_ACCELERATION: 1.0,
            EventType.SUPPLY_CONSTRAINT: 0.5,
            EventType.CAPACITY_EXPANSION: 0.4,
        },
    ),
    SignalDefinition(
        key="memory_market_reaction",
        name="Observed market reaction (coverage-derived)",
        category=SignalCategory.MARKET,
        subject_key="memory",
        description=(
            "Reports that securities prices have already moved. Derived from document "
            "coverage, NOT from market data — no market-data provider is configured, so this "
            "is a weak proxy and is labelled as such wherever it is used."
        ),
        event_weights={EventType.MARKET_REACTION: 1.0},
    ),
)

SIGNALS_BY_KEY = {definition.key: definition for definition in SIGNAL_DEFINITIONS}


def definitions_for_subject(subject_key: str) -> tuple[SignalDefinition, ...]:
    return tuple(d for d in SIGNAL_DEFINITIONS if d.subject_key == subject_key)
