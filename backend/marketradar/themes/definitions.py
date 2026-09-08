"""Theme formation inputs.

Themes are **formed from measured trends**, not declared. What lives here is the small
amount of world knowledge formation needs and cannot derive:

* which subjects are economically adjacent, so co-accelerating signals about them can be
  recognised as one phenomenon rather than three;
* which graph entity a subject anchors to, so the value chain has a starting point;
* human-readable names for subject combinations we have already met.

A subject combination with no template still forms a theme — it just gets a generated,
descriptive name. Formation never depends on a theme having been anticipated.
"""

from __future__ import annotations

from dataclasses import dataclass

from marketradar.domain.enums import EntityType

FORMATION_VERSION = "theme_formation_v1"

#: Subjects that belong to the same economic story. Undirected adjacency; formation takes
#: connected components over this relation.
SUBJECT_ADJACENCY: dict[str, tuple[str, ...]] = {
    "memory": ("ai_infrastructure", "semiconductor_equipment"),
    "ai_infrastructure": ("memory",),
    "semiconductor_equipment": ("memory",),
}

#: Where a subject enters the knowledge graph, and how central it is to a theme built on it.
SUBJECT_ANCHORS: dict[str, tuple[EntityType, str, float]] = {
    "memory": (EntityType.TECHNOLOGY, "memory_requirement", 1.0),
    "ai_infrastructure": (EntityType.TECHNOLOGY, "ai_infrastructure", 0.85),
    "semiconductor_equipment": (EntityType.INDUSTRY, "semiconductor_equipment", 0.6),
}


@dataclass(frozen=True)
class ThemeTemplate:
    """A name and framing for a known subject combination."""

    slug: str
    name: str
    subjects: frozenset[str]
    summary: str
    hypothesis: str


THEME_TEMPLATES: tuple[ThemeTemplate, ...] = (
    ThemeTemplate(
        slug="ai-memory-demand",
        name="AI Memory Demand",
        subjects=frozenset({"memory", "ai_infrastructure"}),
        summary=(
            "Evidence that AI infrastructure deployment is raising memory requirements "
            "faster than memory supply is adjusting."
        ),
        hypothesis=(
            "AI infrastructure growth is increasing demand for high-capacity memory faster "
            "than supply is adjusting, tightening availability and supporting pricing."
        ),
    ),
    ThemeTemplate(
        slug="memory-equipment-cycle",
        name="Memory Equipment Cycle",
        subjects=frozenset({"memory", "semiconductor_equipment"}),
        summary=(
            "Evidence that memory capacity investment is driving order intake at equipment "
            "suppliers."
        ),
        hypothesis=(
            "Memory capacity expansion is translating into a durable increase in "
            "semiconductor equipment demand."
        ),
    ),
)


def template_for(subjects: frozenset[str]) -> ThemeTemplate | None:
    """Best template for a subject set: the most specific one fully contained in it."""
    matches = [t for t in THEME_TEMPLATES if t.subjects <= subjects]
    if not matches:
        return None
    return max(matches, key=lambda t: len(t.subjects))
