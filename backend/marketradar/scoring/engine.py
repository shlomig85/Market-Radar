"""Score computation and persistence.

The rule that shapes this module (ADR-007): **a component whose input is unavailable is
dropped and the remaining weights are renormalised.** It is never replaced by zero, by a
neutral 50, or by a default. The dropped components are recorded on the score and returned
by the API, so a user can see that a number was computed without, say, any market data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode
from marketradar.domain.models import Score, ScoreComponent
from marketradar.errors import ValidationError
from marketradar.scoring.models import ScoreModelSpec


@dataclass(frozen=True)
class ComponentValue:
    """The resolved input for one score component."""

    available: bool
    raw: float | None
    normalized: float | None
    explanation: str
    inputs: dict = field(default_factory=dict)


def value(
    raw: float, normalized: float, explanation: str, **inputs: object
) -> ComponentValue:
    """A component that could be computed."""
    return ComponentValue(
        available=True,
        raw=round(raw, 4),
        normalized=round(max(0.0, min(100.0, normalized)), 4),
        explanation=explanation,
        inputs=dict(inputs),
    )


def unavailable(reason: str, **inputs: object) -> ComponentValue:
    """A component that could not be computed. The reason is shown to the user verbatim."""
    return ComponentValue(
        available=False, raw=None, normalized=None, explanation=reason, inputs=dict(inputs)
    )


@dataclass
class ComputedComponent:
    key: str
    label: str
    available: bool
    raw: float | None
    normalized: float | None
    weight: float
    effective_weight: float
    contribution: float
    explanation: str
    inputs: dict


@dataclass
class ScoreResult:
    """A computed score plus the full decomposition that produced it."""

    model_name: str
    model_version: str
    value: float
    weight_coverage: float
    components: list[ComputedComponent]
    unavailable_components: list[str]

    @property
    def is_computable(self) -> bool:
        return self.weight_coverage > 0.0

    def to_dict(self) -> dict:
        return {
            "model": self.model_name,
            "version": self.model_version,
            "value": self.value,
            "weight_coverage": self.weight_coverage,
            "unavailable_components": self.unavailable_components,
            "components": [
                {
                    "key": c.key,
                    "label": c.label,
                    "available": c.available,
                    "raw": c.raw,
                    "normalized": c.normalized,
                    "weight": c.weight,
                    "effective_weight": c.effective_weight,
                    "contribution": c.contribution,
                    "explanation": c.explanation,
                }
                for c in self.components
            ],
        }


def compute_score(spec: ScoreModelSpec, values: dict[str, ComponentValue]) -> ScoreResult:
    """Compute a score, renormalising over the components that could be resolved."""
    missing = set(spec.component_keys) - set(values)
    if missing:
        # Every component must be explicitly resolved — including explicitly as unavailable.
        # Silence is the one thing not allowed, because it hides a gap in the analysis.
        raise ValidationError(
            "Score model components were not resolved",
            model=spec.name,
            missing=sorted(missing),
        )

    available_weight = sum(
        component.weight for component in spec.components if values[component.key].available
    )
    components: list[ComputedComponent] = []
    total = 0.0

    for spec_component in spec.components:
        resolved = values[spec_component.key]
        effective = (
            spec_component.weight / available_weight
            if resolved.available and available_weight > 0
            else 0.0
        )
        contribution = (resolved.normalized or 0.0) * effective
        total += contribution
        components.append(
            ComputedComponent(
                key=spec_component.key,
                label=spec_component.label,
                available=resolved.available,
                raw=resolved.raw,
                normalized=resolved.normalized,
                weight=spec_component.weight,
                effective_weight=round(effective, 6),
                contribution=round(contribution, 4),
                explanation=resolved.explanation,
                inputs=resolved.inputs,
            )
        )

    return ScoreResult(
        model_name=spec.name,
        model_version=spec.version,
        value=round(max(0.0, min(100.0, total)), 2),
        weight_coverage=round(available_weight, 4),
        components=components,
        unavailable_components=[c.key for c in components if not c.available],
    )


def persist_score(
    session: Session,
    result: ScoreResult,
    subject_type: str,
    subject_id: str,
    data_mode: DataMode,
    computed_at: datetime,
    notes: str | None = None,
) -> Score:
    """Store a score together with every component that produced it."""
    if not result.is_computable:
        raise ValidationError(
            "Refusing to store a score with no available components",
            model=result.model_name,
            subject=subject_id,
        )

    # Re-running the pipeline at the same as-of instant replaces that snapshot rather than
    # colliding with it. Score history is preserved across *different* instants; a repeated
    # computation of the same instant is the same fact, not a new one.
    previous = session.scalar(
        select(Score).where(
            Score.subject_type == subject_type,
            Score.subject_id == subject_id,
            Score.model_name == result.model_name,
            Score.model_version == result.model_version,
            Score.computed_at == computed_at,
        )
    )
    if previous is not None:
        session.execute(delete(ScoreComponent).where(ScoreComponent.score_id == previous.id))
        session.delete(previous)
        session.flush()

    score = Score(
        subject_type=subject_type,
        subject_id=subject_id,
        model_name=result.model_name,
        model_version=result.model_version,
        value=result.value,
        computed_at=computed_at,
        unavailable_components={"keys": result.unavailable_components},
        weight_coverage=result.weight_coverage,
        data_mode=data_mode,
        notes=notes,
    )
    session.add(score)
    session.flush()

    for component in result.components:
        session.add(
            ScoreComponent(
                score_id=score.id,
                key=component.key,
                label=component.label,
                available=component.available,
                raw_input=component.raw,
                normalized=component.normalized,
                weight=component.weight,
                effective_weight=component.effective_weight,
                contribution=component.contribution,
                explanation=component.explanation,
                inputs=component.inputs or None,
            )
        )
    session.flush()
    return score
