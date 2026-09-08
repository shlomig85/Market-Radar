"""Domain vocabularies.

These enums are the shared language of the whole system. Keeping them in one place is what
stops the same concept being spelled three ways in three modules.
"""

from __future__ import annotations

from enum import StrEnum


class DataMode(StrEnum):
    """Provenance mode of a piece of information. Never inferred, always recorded.

    ``UNAVAILABLE`` is a real value: it is what the system reports instead of inventing a
    number. Score components with unavailable inputs are dropped and the remaining weights
    are renormalised (see :mod:`marketradar.scoring`).
    """

    LIVE = "LIVE"
    HISTORICAL = "HISTORICAL"
    DEMO = "DEMO"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def rank(self) -> int:
        """Lower is weaker. Used to compute the weakest mode across a set of inputs."""
        return {"UNAVAILABLE": 0, "DEMO": 1, "HISTORICAL": 2, "LIVE": 3}[self.value]

    @classmethod
    def weakest(cls, modes: list[DataMode] | tuple[DataMode, ...]) -> DataMode:
        """A derived artefact is only as trustworthy as its weakest input."""
        if not modes:
            return cls.UNAVAILABLE
        return min(modes, key=lambda m: m.rank)


class SourceClass(StrEnum):
    """Coarse independence class. Diversity is measured across these, not across publishers."""

    PRIMARY_CORPORATE = "PRIMARY_CORPORATE"
    GOVERNMENT = "GOVERNMENT"
    REGULATORY = "REGULATORY"
    FINANCIAL_MEDIA = "FINANCIAL_MEDIA"
    INDUSTRY = "INDUSTRY"
    TECHNICAL = "TECHNICAL"
    MARKET_DATA = "MARKET_DATA"
    ALTERNATIVE_DATA = "ALTERNATIVE_DATA"
    COMMUNITY = "COMMUNITY"


class SourceType(StrEnum):
    """Specific source type. Drives the base quality prior (see scoring.source_quality)."""

    SEC_FILING = "SEC_FILING"
    COMPANY_FILING = "COMPANY_FILING"
    GOVERNMENT_DATA = "GOVERNMENT_DATA"
    REGULATORY_FILING = "REGULATORY_FILING"
    EARNINGS_TRANSCRIPT = "EARNINGS_TRANSCRIPT"
    COMPANY_PRESS_RELEASE = "COMPANY_PRESS_RELEASE"
    INDUSTRY_ASSOCIATION = "INDUSTRY_ASSOCIATION"
    MAJOR_FINANCIAL_MEDIA = "MAJOR_FINANCIAL_MEDIA"
    SPECIALIST_PUBLICATION = "SPECIALIST_PUBLICATION"
    ANALYST_COMMENTARY = "ANALYST_COMMENTARY"
    TECHNICAL_PUBLICATION = "TECHNICAL_PUBLICATION"
    MARKET_DATA = "MARKET_DATA"
    ALTERNATIVE_DATA = "ALTERNATIVE_DATA"
    COMMUNITY_FORUM = "COMMUNITY_FORUM"
    ANONYMOUS_SOCIAL = "ANONYMOUS_SOCIAL"


class EventType(StrEnum):
    """Atomic, investment-relevant occurrences extracted from documents."""

    DEMAND_ACCELERATION = "DEMAND_ACCELERATION"
    DEMAND_WEAKNESS = "DEMAND_WEAKNESS"
    SUPPLY_CONSTRAINT = "SUPPLY_CONSTRAINT"
    SUPPLY_EXPANSION = "SUPPLY_EXPANSION"
    PRICING_INCREASE = "PRICING_INCREASE"
    PRICING_DECREASE = "PRICING_DECREASE"
    CAPACITY_EXPANSION = "CAPACITY_EXPANSION"
    INVENTORY_DECLINE = "INVENTORY_DECLINE"
    INVENTORY_BUILD = "INVENTORY_BUILD"
    CAPEX_INCREASE = "CAPEX_INCREASE"
    CAPEX_DECREASE = "CAPEX_DECREASE"
    GUIDANCE_RAISE = "GUIDANCE_RAISE"
    GUIDANCE_CUT = "GUIDANCE_CUT"
    TECHNOLOGY_ADOPTION = "TECHNOLOGY_ADOPTION"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    REGULATORY_CHANGE = "REGULATORY_CHANGE"
    CONTRACT_AWARD = "CONTRACT_AWARD"
    MA_ACTIVITY = "MA_ACTIVITY"
    HIRING_INCREASE = "HIRING_INCREASE"
    MARKET_REACTION = "MARKET_REACTION"


class Direction(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class SignalCategory(StrEnum):
    DEMAND = "DEMAND"
    SUPPLY = "SUPPLY"
    PRICING = "PRICING"
    MARGINS = "MARGINS"
    CAPACITY = "CAPACITY"
    INVENTORY = "INVENTORY"
    CAPEX = "CAPEX"
    HIRING = "HIRING"
    TECHNOLOGY = "TECHNOLOGY"
    REGULATORY = "REGULATORY"
    GOVERNMENT = "GOVERNMENT"
    MA = "MA"
    CUSTOMER = "CUSTOMER"
    COMPETITION = "COMPETITION"
    MARKET = "MARKET"
    VALUATION = "VALUATION"
    SENTIMENT = "SENTIMENT"
    SEARCH_INTEREST = "SEARCH_INTEREST"


class TrendMaturity(StrEnum):
    """Lifecycle stage. Assigned by deterministic rules, never by an LLM alone."""

    INVISIBLE = "INVISIBLE"
    EMERGING = "EMERGING"
    DEVELOPING = "DEVELOPING"
    ACCELERATING = "ACCELERATING"
    CONSENSUS = "CONSENSUS"
    CROWDED = "CROWDED"
    MATURE = "MATURE"
    INVALIDATED = "INVALIDATED"

    @property
    def stage(self) -> int:
        return {
            "INVISIBLE": 0,
            "EMERGING": 1,
            "DEVELOPING": 2,
            "ACCELERATING": 3,
            "CONSENSUS": 4,
            "CROWDED": 5,
            "MATURE": 6,
            "INVALIDATED": -1,
        }[self.value]


class MarketAwareness(StrEnum):
    """How much the market appears to already know. ``UNKNOWN`` means low awareness;
    absence of the measurement is expressed with ``DataMode.UNAVAILABLE`` instead."""

    UNKNOWN = "UNKNOWN"
    EMERGING = "EMERGING"
    DEVELOPING = "DEVELOPING"
    CONSENSUS = "CONSENSUS"
    CROWDED = "CROWDED"


class EntityType(StrEnum):
    COMPANY = "COMPANY"
    INDUSTRY = "INDUSTRY"
    TECHNOLOGY = "TECHNOLOGY"
    PRODUCT = "PRODUCT"
    THEME = "THEME"
    COMMODITY = "COMMODITY"
    REGULATION = "REGULATION"
    COUNTRY = "COUNTRY"


class RelationshipType(StrEnum):
    """Knowledge-graph edge types. Every edge carries confidence and may carry evidence."""

    SUPPLIES = "SUPPLIES"
    BUYS_FROM = "BUYS_FROM"
    COMPETES_WITH = "COMPETES_WITH"
    DEPENDS_ON = "DEPENDS_ON"
    BENEFITS_FROM = "BENEFITS_FROM"
    THREATENS = "THREATENS"
    SUBSTITUTES_FOR = "SUBSTITUTES_FOR"
    PRODUCES = "PRODUCES"
    USES = "USES"
    REGULATES = "REGULATES"
    INVESTS_IN = "INVESTS_IN"
    DRIVES_DEMAND_FOR = "DRIVES_DEMAND_FOR"


class ExposureRole(StrEnum):
    """How a company relates to a theme."""

    DIRECT_BENEFICIARY = "DIRECT_BENEFICIARY"
    INDIRECT_BENEFICIARY = "INDIRECT_BENEFICIARY"
    SUPPLIER = "SUPPLIER"
    CUSTOMER = "CUSTOMER"
    COMPETITOR = "COMPETITOR"
    SUBSTITUTE = "SUBSTITUTE"
    LOSER = "LOSER"
    INFRASTRUCTURE_BENEFICIARY = "INFRASTRUCTURE_BENEFICIARY"


class ClaimType(StrEnum):
    """Epistemic status of a statement. These are never blurred in a report."""

    FACT = "FACT"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"
    FORECAST = "FORECAST"


class ResearchDimension(StrEnum):
    """The dimensions a research plan must cover. Counter-evidence is mandatory."""

    DEMAND = "DEMAND"
    SUPPLY = "SUPPLY"
    PRICING = "PRICING"
    CAPACITY = "CAPACITY"
    CUSTOMERS = "CUSTOMERS"
    COMPETITION = "COMPETITION"
    TECHNOLOGY = "TECHNOLOGY"
    COMPANY_EXPOSURE = "COMPANY_EXPOSURE"
    MARKET_EXPECTATIONS = "MARKET_EXPECTATIONS"
    RISKS = "RISKS"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    HISTORICAL_ANALOGUE = "HISTORICAL_ANALOGUE"


class FindingStance(StrEnum):
    SUPPORTING = "SUPPORTING"
    CONTRADICTING = "CONTRADICTING"
    NEUTRAL = "NEUTRAL"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TIMED_OUT = "TIMED_OUT"


class ClusterMethod(StrEnum):
    """How a document was attached to an evidence cluster (its ancestry reason)."""

    ORIGIN = "ORIGIN"
    EXACT_HASH = "EXACT_HASH"
    NEAR_DUPLICATE = "NEAR_DUPLICATE"
    DECLARED_ORIGIN = "DECLARED_ORIGIN"


class ProviderCapability(StrEnum):
    NEWS_SEARCH = "NEWS_SEARCH"
    FILINGS = "FILINGS"
    MARKET_DATA = "MARKET_DATA"
    COMPANY_DATA = "COMPANY_DATA"
