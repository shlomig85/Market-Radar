"""Evidence independence.

Converts a bag of evidence into the numbers that actually matter for signal strength:
how many *independent* confirmations exist, how diverse they are, and how much of the
evidence comes from primary sources.

Every downstream strength calculation consumes these, never a raw document count. This is
the system's principal defence against mistaking amplification for confirmation.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from marketradar.domain.enums import SourceClass


@dataclass(frozen=True)
class EvidenceDescriptor:
    """What independence scoring needs to know about a single evidence item."""

    evidence_id: str
    cluster_id: str
    source_class: SourceClass
    source_quality: int
    is_primary: bool


@dataclass(frozen=True)
class IndependenceProfile:
    """The independence characteristics of an evidence set."""

    evidence_count: int
    independent_source_count: int
    underlying_event_count: int
    source_diversity: float
    primary_source_ratio: float
    avg_source_quality: float
    #: ``independent_source_count / evidence_count``. 1.0 means nothing was amplified;
    #: 0.2 means five pieces of evidence traced back to a single announcement.
    amplification_ratio: float

    @property
    def is_single_sourced(self) -> bool:
        return self.independent_source_count <= 1


#: Source classes that count as primary evidence: the entity itself, or an official record.
PRIMARY_CLASSES = frozenset(
    {SourceClass.PRIMARY_CORPORATE, SourceClass.REGULATORY, SourceClass.GOVERNMENT}
)


def normalised_entropy(counts: list[int]) -> float:
    """Shannon entropy of a distribution, scaled to 0..1.

    One category -> 0.0 (no diversity). A perfectly even spread -> 1.0. Using entropy
    rather than a raw category count means three sources spread evenly across three classes
    score higher than three sources where two are the same class.
    """
    total = sum(counts)
    if total <= 0 or len(counts) <= 1:
        return 0.0
    entropy = -sum((c / total) * math.log(c / total) for c in counts if c > 0)
    # max(0.0, ...) guards against -0.0 from floating point when entropy is zero.
    return min(1.0, max(0.0, entropy / math.log(len(counts))))


def profile(evidence: list[EvidenceDescriptor]) -> IndependenceProfile:
    """Compute the independence profile of an evidence set."""
    if not evidence:
        return IndependenceProfile(0, 0, 0, 0.0, 0.0, 0.0, 0.0)

    clusters = {item.cluster_id for item in evidence}
    # Diversity is measured over independent clusters, not over evidence items: five
    # rewrites of one press release must not read as five diverse sources.
    class_by_cluster: dict[str, SourceClass] = {}
    quality_by_cluster: dict[str, int] = {}
    primary_clusters: set[str] = set()
    for item in evidence:
        # Highest-authority member represents its cluster.
        if item.source_quality >= quality_by_cluster.get(item.cluster_id, -1):
            quality_by_cluster[item.cluster_id] = item.source_quality
            class_by_cluster[item.cluster_id] = item.source_class
        if item.is_primary:
            primary_clusters.add(item.cluster_id)

    class_counts = Counter(class_by_cluster.values())
    # Spread across the full class vocabulary so "3 of 9 possible classes" is penalised
    # relative to "3 of 3 classes represented evenly".
    counts = [class_counts.get(source_class, 0) for source_class in SourceClass]

    independent = len(clusters)
    return IndependenceProfile(
        evidence_count=len(evidence),
        independent_source_count=independent,
        underlying_event_count=independent,
        source_diversity=normalised_entropy(counts),
        primary_source_ratio=len(primary_clusters) / independent,
        avg_source_quality=sum(quality_by_cluster.values()) / independent,
        amplification_ratio=independent / len(evidence),
    )
