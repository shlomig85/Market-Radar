"""Company exposure scoring and persistence."""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, EntityType
from marketradar.domain.models import Company, Event, Theme, ThemeCompanyExposure
from marketradar.mapping.value_chain import TRAVERSAL_VERSION, Anchor, ValueChainTraverser

#: How fast anchor weight saturates in the number of independent clusters naming a company.
#: One cluster is a single report and should not anchor a theme strongly; three is a story.
#: Same saturating form as signal strength, so the two read consistently.
EVIDENCE_SATURATION = 2.0


def evidence_anchors(
    session: Session,
    subjects: set[str],
    window_start: datetime,
    as_of: datetime,
    subject_weights: dict[str, float],
) -> list[Anchor]:
    """Companies the theme's OWN evidence names, as first-order anchors.

    A graph built from real filings is mostly company-to-company: filings say who supplies
    whom, and rarely say "we produce high-bandwidth memory" in the vocabulary a concept
    lexicon happens to know. Anchoring only on concept nodes therefore produced a real
    theme with **zero** companies on a live SEC run — the traversal started at nodes the
    graph did not contain.

    The companies named by the evidence that formed the theme are the entry point that is
    always available, and they are first-order by construction: the evidence is *about*
    them. Traversal then extends outward from there over the extracted edges.

    Weight saturates in the number of independent evidence clusters, so a company named by
    one report does not anchor a theme as hard as one named by five.
    """
    rows = session.execute(
        select(Event.entity_key, Event.subject_key, Event.cluster_id, Event.data_mode).where(
            Event.entity_type == EntityType.COMPANY,
            Event.occurred_at >= window_start,
            Event.occurred_at <= as_of,
            # Temporal-integrity gate: see Event.knowable_at. A backtest must not anchor a
            # theme on a company that evidence had not yet named.
            Event.knowable_at <= as_of,
        )
    ).all()

    clusters: dict[str, set[str]] = {}
    modes: dict[str, list[DataMode]] = {}
    weights: dict[str, float] = {}
    for entity_key, subject_key, cluster_id, data_mode in rows:
        if not entity_key or (subjects and subject_key not in subjects):
            continue
        clusters.setdefault(entity_key, set()).add(cluster_id or "")
        modes.setdefault(entity_key, []).append(data_mode)
        weights[entity_key] = max(
            weights.get(entity_key, 0.0), subject_weights.get(subject_key or "", 0.6)
        )

    known = {key for (key,) in session.execute(select(Company.key)).all()}
    anchors: list[Anchor] = []
    for entity_key, cluster_ids in sorted(clusters.items()):
        if entity_key not in known:
            continue
        count = len(cluster_ids)
        saturation = 1.0 - math.exp(-count / EVIDENCE_SATURATION)
        plural = "s" if count != 1 else ""
        anchors.append(
            Anchor(
                entity_type=EntityType.COMPANY,
                entity_key=entity_key,
                weight=round(weights[entity_key] * saturation, 6),
                data_mode=DataMode.weakest(modes[entity_key]),
                reason=(
                    f"Named directly by {count} independent evidence cluster{plural} "
                    "supporting this theme."
                ),
            )
        )
    return anchors


def compute_exposures(
    session: Session, theme: Theme, anchors: list[Anchor], computed_at: datetime
) -> list[ThemeCompanyExposure]:
    """Map a theme onto companies and persist the result with its causal path.

    The stored ``path`` is the explanation: a reader can follow every hop, see the weight
    and confidence of each edge, and disagree with a specific link rather than with an
    opaque number.
    """
    traverser = ValueChainTraverser(session)
    paths = traverser.traverse(anchors)

    companies = {c.key: c for c in session.scalars(select(Company)).all()}
    existing = {
        row.company_id: row
        for row in session.scalars(
            select(ThemeCompanyExposure).where(
                ThemeCompanyExposure.theme_id == theme.id,
                ThemeCompanyExposure.computation_version == TRAVERSAL_VERSION,
            )
        ).all()
    }

    results: list[ThemeCompanyExposure] = []
    for path in paths:
        company = companies.get(path.company_key)
        if company is None:
            continue
        row = existing.get(company.id)
        if row is None:
            row = ThemeCompanyExposure(
                theme_id=theme.id,
                company_id=company.id,
                computation_version=TRAVERSAL_VERSION,
                role=path.role,
                order_of_effect=path.order_of_effect,
                exposure_score=0.0,
                confidence=0.0,
                data_mode=path.data_mode,
                computed_at=computed_at,
            )
            session.add(row)
        row.role = path.role
        row.order_of_effect = path.order_of_effect
        row.exposure_score = path.exposure_score
        row.confidence = round(path.confidence * 100.0, 2)
        row.path = {
            "hops": [
                {
                    "from": f"{hop.from_type}:{hop.from_key}",
                    "relationship": hop.relationship,
                    "direction": hop.direction,
                    "to": f"{hop.to_type}:{hop.to_key}",
                    "edge_weight": hop.weight,
                    "edge_confidence": hop.confidence,
                }
                for hop in path.hops
            ],
            "traversal_version": TRAVERSAL_VERSION,
            # Says which kind of explanation this row carries: a traversal over graph edges,
            # or a direct naming by the theme's own evidence. Never empty, so no exposure is
            # ever a bare number with nothing behind it.
            "anchored_by": "evidence" if path.anchor_reason else "traversal",
        }
        row.rationale = path.describe()
        row.data_mode = path.data_mode
        row.computed_at = computed_at
        results.append(row)

    session.flush()
    return results
