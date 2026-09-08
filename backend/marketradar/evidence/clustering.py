"""Information ancestry.

Ten articles rewriting one announcement are one confirmation, not ten. This module decides
which documents belong to the same underlying event, using — in strict precedence order:

1. **Declared origin** (``origin_ref``): the provider or the document itself says which
   announcement it reports. Strongest available evidence, and free.
2. **Exact content hash** across sources: verbatim syndication.
3. **Near-duplicate shingle similarity**: rewritten or lightly edited copies.

Anything left over is its own cluster of one. The cluster's *origin* is the earliest member,
with the highest-authority source breaking ties — because the primary announcement, not the
first outlet to rewrite it, is the thing that actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from marketradar.domain.enums import ClusterMethod
from marketradar.ingestion.hashing import jaccard, shingles


@dataclass(frozen=True)
class ClusterCandidate:
    """The minimum a document must expose to be clustered."""

    document_id: str
    content_hash: str
    text: str
    event_at: datetime
    published_at: datetime
    source_quality: int
    origin_ref: str | None = None


@dataclass
class ClusterAssignment:
    """One cluster: its members, their attachment reason, and its origin document."""

    cluster_key: str
    members: dict[str, ClusterMethod] = field(default_factory=dict)
    origin_document_id: str | None = None
    label: str | None = None

    @property
    def size(self) -> int:
        return len(self.members)


def assign_clusters(
    candidates: list[ClusterCandidate], similarity_threshold: float = 0.60
) -> list[ClusterAssignment]:
    """Group documents into ancestry clusters.

    Deterministic: candidates are processed in a stable order (event time, then publication
    time, then document id), so the same corpus always yields the same clustering.
    """
    ordered = sorted(
        candidates, key=lambda c: (c.event_at, c.published_at, c.document_id)
    )

    clusters: list[ClusterAssignment] = []
    by_origin_ref: dict[str, ClusterAssignment] = {}
    by_hash: dict[str, ClusterAssignment] = {}
    shingle_cache: dict[str, frozenset[str]] = {}
    cluster_shingles: dict[str, list[frozenset[str]]] = {}

    def _shingles(candidate: ClusterCandidate) -> frozenset[str]:
        if candidate.document_id not in shingle_cache:
            shingle_cache[candidate.document_id] = shingles(candidate.text)
        return shingle_cache[candidate.document_id]

    for candidate in ordered:
        target: ClusterAssignment | None = None
        method = ClusterMethod.ORIGIN

        # 1. declared ancestry
        if candidate.origin_ref and candidate.origin_ref in by_origin_ref:
            target = by_origin_ref[candidate.origin_ref]
            method = ClusterMethod.DECLARED_ORIGIN

        # 2. verbatim republication
        if target is None and candidate.content_hash in by_hash:
            target = by_hash[candidate.content_hash]
            method = ClusterMethod.EXACT_HASH

        # 3. near duplicate
        if target is None:
            candidate_shingles = _shingles(candidate)
            best_score = 0.0
            best_cluster: ClusterAssignment | None = None
            for cluster in clusters:
                for member_shingles in cluster_shingles.get(cluster.cluster_key, []):
                    score = jaccard(candidate_shingles, member_shingles)
                    if score > best_score:
                        best_score, best_cluster = score, cluster
            if best_cluster is not None and best_score >= similarity_threshold:
                target = best_cluster
                method = ClusterMethod.NEAR_DUPLICATE

        if target is None:
            target = ClusterAssignment(cluster_key=f"cluster:{candidate.document_id}")
            clusters.append(target)
            method = ClusterMethod.ORIGIN

        target.members[candidate.document_id] = method
        cluster_shingles.setdefault(target.cluster_key, []).append(_shingles(candidate))
        by_hash.setdefault(candidate.content_hash, target)
        if candidate.origin_ref:
            by_origin_ref.setdefault(candidate.origin_ref, target)
            target.label = target.label or candidate.origin_ref

    _assign_origins(clusters, {c.document_id: c for c in candidates})
    return clusters


def _assign_origins(
    clusters: list[ClusterAssignment], by_id: dict[str, ClusterCandidate]
) -> None:
    """Pick each cluster's origin: earliest event, then highest source authority."""
    for cluster in clusters:
        members = [by_id[doc_id] for doc_id in cluster.members]
        origin = min(
            members,
            key=lambda c: (c.event_at, -c.source_quality, c.published_at, c.document_id),
        )
        cluster.origin_document_id = origin.document_id
        # The origin is, by definition, not a copy of anything.
        cluster.members[origin.document_id] = ClusterMethod.ORIGIN
