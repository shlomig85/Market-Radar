"""Theme formation.

Groups *co-accelerating* signals about economically adjacent subjects into a theme.

The gate is deliberately a gate on **change**, not on volume: a signal qualifies only if its
acceleration against its own baseline clears a threshold and it has more than one
independent source behind it. A subject can be enormously well covered and never form a
theme, which is the intended behaviour — the product's job is to surface what is changing,
not what is popular.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode
from marketradar.domain.models import Signal, SignalObservation, Theme, ThemeSignal, Trend
from marketradar.logging import get_logger
from marketradar.signals.definitions import SIGNALS_BY_KEY
from marketradar.themes.definitions import (
    FORMATION_VERSION,
    SUBJECT_ADJACENCY,
    ThemeTemplate,
    template_for,
)

log = get_logger(__name__)

#: A signal must be moving this much against its own baseline to join a theme.
MIN_ACCELERATION = 20.0

#: ...and be corroborated by at least this many independent sources in the window.
MIN_INDEPENDENT_SOURCES = 2


@dataclass
class SignalActivation:
    """A signal that passed the formation gate, with the numbers that let it through."""

    signal: Signal
    trend: Trend
    independent_sources: int
    subject_key: str

    @property
    def weight(self) -> float:
        """How heavily this signal counts toward its theme."""
        return round(
            (abs(self.trend.acceleration) / 100.0) * (self.trend.observation_strength / 100.0),
            6,
        )


@dataclass
class FormedTheme:
    theme: Theme
    activations: list[SignalActivation] = field(default_factory=list)
    template: ThemeTemplate | None = None
    created: bool = False


def independent_sources_in_window(
    session: Session, signal: Signal, start: datetime, end: datetime
) -> int:
    """Distinct evidence clusters supporting a signal across the whole window.

    Counted across the window rather than as a per-bucket peak: two independent sources
    reporting in consecutive weeks are still two independent sources, and taking the maximum
    of the weekly buckets would score that as one. The cluster ids come from the inputs
    stored on each observation, so this is a read of what was already computed rather than a
    second, divergent calculation.
    """
    observations = session.scalars(
        select(SignalObservation).where(
            SignalObservation.signal_id == signal.id,
            SignalObservation.bucket_start >= start,
            SignalObservation.bucket_start < end,
        )
    ).all()
    clusters: set[str] = set()
    for observation in observations:
        for contribution in (observation.inputs or {}).get("contributions", []):
            cluster_id = contribution.get("cluster_id")
            if cluster_id:
                clusters.add(cluster_id)
    return len(clusters)


def _connected_components(subjects: set[str]) -> list[frozenset[str]]:
    """Group subjects into components over the adjacency relation."""
    remaining = set(subjects)
    components: list[frozenset[str]] = []
    while remaining:
        seed = remaining.pop()
        component = {seed}
        queue = [seed]
        while queue:
            current = queue.pop()
            for neighbour in SUBJECT_ADJACENCY.get(current, ()):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.add(neighbour)
                    queue.append(neighbour)
        components.append(frozenset(component))
    return components


def form_themes(
    session: Session,
    trends: dict[str, Trend],
    as_of: datetime,
    min_acceleration: float = MIN_ACCELERATION,
    min_independent_sources: int = MIN_INDEPENDENT_SOURCES,
) -> list[FormedTheme]:
    """Form or update themes from the current trend set."""
    activations: list[SignalActivation] = []
    for signal_key, trend in trends.items():
        definition = SIGNALS_BY_KEY.get(signal_key)
        if definition is None:
            continue
        signal = session.scalar(select(Signal).where(Signal.key == signal_key))
        if signal is None:
            continue
        if trend.acceleration < min_acceleration:
            log.debug(
                "theme.signal_rejected",
                signal=signal_key,
                reason="acceleration_below_threshold",
                acceleration=trend.acceleration,
            )
            continue
        independent = independent_sources_in_window(
            session, signal, trend.observation_start, trend.observation_end
        )
        if independent < min_independent_sources:
            log.debug(
                "theme.signal_rejected",
                signal=signal_key,
                reason="insufficient_independent_sources",
                independent_sources=independent,
            )
            continue
        activations.append(
            SignalActivation(
                signal=signal,
                trend=trend,
                independent_sources=independent,
                subject_key=definition.subject_key,
            )
        )

    if not activations:
        return []

    formed: list[FormedTheme] = []
    for component in _connected_components({a.subject_key for a in activations}):
        members = [a for a in activations if a.subject_key in component]
        if not members:
            continue
        template = template_for(component)
        slug = template.slug if template else _generated_slug(component)
        name = template.name if template else _generated_name(component)
        summary = template.summary if template else _generated_summary(component, members)

        theme = session.scalar(select(Theme).where(Theme.slug == slug))
        created = theme is None
        if theme is None:
            theme = Theme(
                slug=slug,
                name=name,
                summary=summary,
                first_detected_at=as_of,
                last_updated_at=as_of,
                data_mode=DataMode.weakest([a.trend.data_mode for a in members]),
            )
            session.add(theme)
            session.flush()
        theme.name = name
        theme.summary = summary
        theme.last_updated_at = as_of
        theme.data_mode = DataMode.weakest([a.trend.data_mode for a in members])
        theme.anchor_entities = {"subjects": sorted(component), "version": FORMATION_VERSION}

        existing = {
            link.signal_id: link
            for link in session.scalars(
                select(ThemeSignal).where(ThemeSignal.theme_id == theme.id)
            ).all()
        }
        for activation in members:
            link = existing.get(activation.signal.id)
            if link is None:
                link = ThemeSignal(theme_id=theme.id, signal_id=activation.signal.id, weight=0.0)
                session.add(link)
            link.weight = activation.weight
            activation.trend.theme_id = theme.id

        session.flush()
        formed.append(
            FormedTheme(theme=theme, activations=members, template=template, created=created)
        )

    return formed


def _generated_slug(subjects: frozenset[str]) -> str:
    return "-".join(sorted(s.replace("_", "-") for s in subjects)) + "-change"


def _generated_name(subjects: frozenset[str]) -> str:
    readable = ", ".join(s.replace("_", " ").title() for s in sorted(subjects))
    return f"Emerging change: {readable}"


def _generated_summary(subjects: frozenset[str], members: list[SignalActivation]) -> str:
    signals = ", ".join(sorted(a.signal.name for a in members))
    readable = ", ".join(sorted(s.replace("_", " ") for s in subjects))
    return (
        f"Co-accelerating signals ({signals}) indicate a change across {readable}. "
        "This theme was formed automatically from measured signal acceleration and has no "
        "curated framing yet."
    )
