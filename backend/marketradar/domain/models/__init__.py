"""SQLAlchemy models, grouped by bounded concern.

Import order matters only for relationship resolution; importing this package registers
every model on ``Base.metadata`` (which is what Alembic autogenerate reads).
"""

from marketradar.domain.models.entities import (
    Company,
    EntityRelationship,
    Industry,
    Security,
)
from marketradar.domain.models.intelligence import (
    Event,
    EventEvidence,
    Signal,
    SignalObservation,
    Subject,
    Theme,
    ThemeCompanyExposure,
    ThemeSignal,
    Trend,
)
from marketradar.domain.models.provenance import (
    EvidenceCluster,
    EvidenceItem,
    Source,
    SourceDocument,
)
from marketradar.domain.models.research import (
    AgentRun,
    FindingEvidence,
    Hypothesis,
    ResearchFinding,
    ResearchPlan,
    ResearchQuestion,
    ResearchReport,
    ResearchRun,
    SearchRun,
)
from marketradar.domain.models.scoring import Score, ScoreComponent

__all__ = [
    "AgentRun",
    "Company",
    "EntityRelationship",
    "Event",
    "EventEvidence",
    "EvidenceCluster",
    "EvidenceItem",
    "FindingEvidence",
    "Hypothesis",
    "Industry",
    "ResearchFinding",
    "ResearchPlan",
    "ResearchQuestion",
    "ResearchReport",
    "ResearchRun",
    "Score",
    "ScoreComponent",
    "SearchRun",
    "Security",
    "Signal",
    "Subject",
    "SignalObservation",
    "Source",
    "SourceDocument",
    "Theme",
    "ThemeCompanyExposure",
    "ThemeSignal",
    "Trend",
]
