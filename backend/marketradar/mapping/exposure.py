"""Company exposure scoring and persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.models import Company, Theme, ThemeCompanyExposure
from marketradar.mapping.value_chain import TRAVERSAL_VERSION, Anchor, ValueChainTraverser


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
        }
        row.rationale = path.describe()
        row.data_mode = path.data_mode
        row.computed_at = computed_at
        results.append(row)

    session.flush()
    return results
