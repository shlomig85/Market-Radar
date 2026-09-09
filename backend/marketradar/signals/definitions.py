"""Signal definitions.

A signal is *data*, not code: it names a measurable phenomenon and states which event types
move it and in which direction. Adding a signal means adding a definition, never editing the
engine.

The sign lives here rather than on the event because the same event means different things
to different signals: a capacity expansion is positive for capital-investment intensity and
negative for supply tightness, and both readings are correct.
"""

from __future__ import annotations

from collections.abc import Iterable
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


@dataclass(frozen=True)
class SignalTemplate:
    """A measurable phenomenon, stated independently of what it is measured about.

    The seven hand-written definitions this replaced carried subject-specific keys and
    identical weights: "demand acceleration is positive for demand" is a fact about the
    *category*, not about memory. Keeping them per-subject meant a newly discovered subject
    got evidence attached and then measured by nothing — tagged, and invisible (audit C6).

    Templates are instantiated against whatever subjects the corpus turns out to be about,
    so discovery flows through to signals, trends and themes without anyone editing code.
    """

    slug: str
    label: str
    category: SignalCategory
    description: str
    event_weights: dict[EventType, float]


SIGNAL_TEMPLATES: tuple[SignalTemplate, ...] = (
    SignalTemplate(
        slug="demand",
        label="demand",
        category=SignalCategory.DEMAND,
        description="Evidence that end demand for {subject} is changing.",
        event_weights={
            EventType.DEMAND_ACCELERATION: 1.0,
            EventType.DEMAND_WEAKNESS: -1.0,
            EventType.CONTRACT_AWARD: 0.5,
        },
    ),
    SignalTemplate(
        slug="supply_tightness",
        label="supply tightness",
        category=SignalCategory.SUPPLY,
        description=(
            "Evidence that supply of {subject} is tight relative to demand. Capacity "
            "expansion counts negatively: added supply loosens the market."
        ),
        event_weights={
            EventType.SUPPLY_CONSTRAINT: 1.0,
            EventType.INVENTORY_DECLINE: 0.8,
            EventType.INVENTORY_BUILD: -0.8,
            EventType.SUPPLY_EXPANSION: -1.0,
            EventType.CAPACITY_EXPANSION: -0.6,
        },
    ),
    SignalTemplate(
        slug="pricing",
        label="pricing",
        category=SignalCategory.PRICING,
        description="Evidence of change in {subject} contract or spot pricing.",
        event_weights={
            EventType.PRICING_INCREASE: 1.0,
            EventType.PRICING_DECREASE: -1.0,
        },
    ),
    SignalTemplate(
        slug="capital_investment",
        label="capital investment",
        category=SignalCategory.CAPEX,
        description="Capital being committed to {subject} capacity.",
        event_weights={
            EventType.CAPEX_INCREASE: 1.0,
            EventType.CAPEX_DECREASE: -1.0,
            EventType.CAPACITY_EXPANSION: 0.6,
        },
    ),
    SignalTemplate(
        slug="market_reaction",
        label="observed market reaction (coverage-derived)",
        category=SignalCategory.MARKET,
        description=(
            "Reports that securities prices exposed to {subject} have already moved. "
            "Derived from document coverage, NOT from market data — no market-data provider "
            "is configured, so this is a weak proxy and is labelled as such wherever used."
        ),
        event_weights={EventType.MARKET_REACTION: 1.0},
    ),
)


def definitions_for_subjects(
    subject_keys: Iterable[str], labels: dict[str, str] | None = None
) -> tuple[SignalDefinition, ...]:
    """Instantiate every template against every subject.

    The signal key is ``{subject}_{template}``, which keeps it stable across runs and
    readable in a trace — a stored trend for ``grid_storage_pricing`` says what it measures
    without a lookup.
    """
    labels = labels or {}
    definitions: list[SignalDefinition] = []
    for subject_key in subject_keys:
        label = labels.get(subject_key, subject_key.replace("_", " "))
        for template in SIGNAL_TEMPLATES:
            definitions.append(
                SignalDefinition(
                    key=f"{subject_key}_{template.slug}",
                    name=f"{label.capitalize()} {template.label}",
                    category=template.category,
                    subject_key=subject_key,
                    description=template.description.format(subject=label),
                    event_weights=dict(template.event_weights),
                )
            )
    return tuple(definitions)


#: The built-in lexicon subjects, instantiated. Kept so the development corpus and anything
#: importing this constant keeps working; the pipeline builds its own set from whatever the
#: corpus turned out to be about.
SIGNAL_DEFINITIONS: tuple[SignalDefinition, ...] = definitions_for_subjects(
    ("memory", "ai_infrastructure", "semiconductor_equipment")
)

SIGNALS_BY_KEY = {definition.key: definition for definition in SIGNAL_DEFINITIONS}


def definitions_for_subject(subject_key: str) -> tuple[SignalDefinition, ...]:
    return tuple(d for d in SIGNAL_DEFINITIONS if d.subject_key == subject_key)
