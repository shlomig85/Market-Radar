"""Versioned, explainable scoring."""

from marketradar.scoring.engine import (
    ComponentValue,
    ScoreResult,
    compute_score,
    persist_score,
    unavailable,
    value,
)
from marketradar.scoring.models import (
    CONFIDENCE_V1,
    OPPORTUNITY_V1,
    TREND_V1,
    ComponentSpec,
    ScoreModelSpec,
)

__all__ = [
    "CONFIDENCE_V1",
    "OPPORTUNITY_V1",
    "TREND_V1",
    "ComponentSpec",
    "ComponentValue",
    "ScoreModelSpec",
    "ScoreResult",
    "compute_score",
    "persist_score",
    "unavailable",
    "value",
]
