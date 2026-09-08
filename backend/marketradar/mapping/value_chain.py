"""Value-chain traversal.

Given a theme's anchor concepts, walk the knowledge graph outward to find the companies
exposed to it, at first, second and third order.

The traversal is generic. It knows about *edge semantics* — "who produces a thing whose
demand is rising", "who supplies that producer" — and nothing about memory, AI or
semiconductors. The AI-memory chain in the demo universe is rows in
``entity_relationships``; replacing those rows replaces the chain, which is what makes this
a reusable engine rather than one hardcoded example (architecture §8).

Confidence decays multiplicatively with each hop, because a chain of plausible links is
less certain than any single link in it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import (
    DataMode,
    EntityType,
    ExposureRole,
    RelationshipType,
)
from marketradar.domain.models import Company, EntityRelationship

TRAVERSAL_VERSION = "value_chain_v1"

#: How many hops out from the anchors we are willing to reason about. Beyond three, a chain
#: of plausible links stops being evidence about anything.
MAX_HOPS = 4

#: Per-hop confidence penalty applied on top of each edge's own confidence.
HOP_DECAY = 0.85


@dataclass(frozen=True)
class Anchor:
    """A concept the theme is anchored to, and how central it is to that theme."""

    entity_type: EntityType
    entity_key: str
    weight: float = 1.0


@dataclass(frozen=True)
class Hop:
    """One traversed edge, kept so the path can be shown as the explanation."""

    from_type: str
    from_key: str
    relationship: str
    to_type: str
    to_key: str
    direction: str
    weight: float
    confidence: float


@dataclass
class ExposurePath:
    """A company reached from a theme anchor, and how it was reached."""

    company_key: str
    role: ExposureRole
    order_of_effect: int
    weight: float
    confidence: float
    hops: list[Hop] = field(default_factory=list)
    data_mode: DataMode = DataMode.UNAVAILABLE

    @property
    def exposure_score(self) -> float:
        return round(min(100.0, max(0.0, self.weight * 100.0)), 2)

    def describe(self) -> str:
        """A one-line, human-checkable rendering of the causal path."""
        if not self.hops:
            return "Directly anchored to the theme."
        parts = [f"{self.hops[0].from_key}"]
        for hop in self.hops:
            arrow = "->" if hop.direction == "forward" else "<-"
            parts.append(f"{arrow}[{hop.relationship}]{arrow} {hop.to_key}")
        return " ".join(parts)


#: How each edge type is interpreted when traversed, and what role it confers.
#:
#: ``forward`` follows the edge from source to target; ``reverse`` walks it backwards, which
#: is how "demand for HBM is rising" reaches "the company that produces HBM".
_TRAVERSALS: dict[tuple[RelationshipType, str], tuple[ExposureRole, float]] = {
    # demand propagating down a chain of concepts
    (RelationshipType.DRIVES_DEMAND_FOR, "forward"): (ExposureRole.DIRECT_BENEFICIARY, 1.0),
    # a company that makes the thing whose demand is rising
    (RelationshipType.PRODUCES, "reverse"): (ExposureRole.DIRECT_BENEFICIARY, 1.0),
    # a company that supplies that producer
    (RelationshipType.SUPPLIES, "reverse"): (ExposureRole.SUPPLIER, 0.8),
    # "we depend on X" states the same linkage from the buyer's side: walking forward off
    # the dependent company reaches the input it depends on, which is its supplier.
    (RelationshipType.DEPENDS_ON, "forward"): (ExposureRole.SUPPLIER, 0.8),
    # a company that buys from that producer: exposed, but rising input cost cuts both ways
    (RelationshipType.BUYS_FROM, "reverse"): (ExposureRole.CUSTOMER, 0.5),
    # competitors of a beneficiary share the market backdrop
    (RelationshipType.COMPETES_WITH, "forward"): (ExposureRole.COMPETITOR, 0.6),
    (RelationshipType.COMPETES_WITH, "reverse"): (ExposureRole.COMPETITOR, 0.6),
    # a substitute is exposed in the opposite direction
    (RelationshipType.SUBSTITUTES_FOR, "reverse"): (ExposureRole.SUBSTITUTE, 0.5),
    # capital flowing into a concept
    (RelationshipType.INVESTS_IN, "reverse"): (ExposureRole.INFRASTRUCTURE_BENEFICIARY, 0.6),
}


@dataclass
class _Node:
    entity_type: EntityType
    entity_key: str
    weight: float
    confidence: float
    order: int
    hops: list[Hop]
    role: ExposureRole | None
    modes: list[DataMode]


class ValueChainTraverser:
    """Breadth-first traversal of the knowledge graph from a theme's anchors."""

    def __init__(self, session: Session, max_hops: int = MAX_HOPS) -> None:
        self.session = session
        self.max_hops = max_hops
        self._outgoing: dict[tuple[str, str], list[EntityRelationship]] = {}
        self._incoming: dict[tuple[str, str], list[EntityRelationship]] = {}
        self._load_graph()

    def _load_graph(self) -> None:
        for edge in self.session.scalars(select(EntityRelationship)).all():
            self._outgoing.setdefault(
                (edge.source_entity_type.value, edge.source_entity_key), []
            ).append(edge)
            self._incoming.setdefault(
                (edge.target_entity_type.value, edge.target_entity_key), []
            ).append(edge)

    def traverse(self, anchors: list[Anchor]) -> list[ExposurePath]:
        """Return the best exposure path for every company reachable from the anchors."""
        company_keys = {
            company.key for company in self.session.scalars(select(Company)).all()
        }

        best: dict[str, ExposurePath] = {}
        # Guards against revisiting a node on a weaker path, which is what stops cycles
        # (A competes with B competes with A) from looping.
        seen: dict[tuple[str, str], float] = {}
        queue: deque[_Node] = deque()

        for anchor in anchors:
            node = _Node(
                entity_type=anchor.entity_type,
                entity_key=anchor.entity_key,
                weight=anchor.weight,
                confidence=1.0,
                order=0,
                hops=[],
                role=None,
                modes=[],
            )
            queue.append(node)
            seen[(anchor.entity_type.value, anchor.entity_key)] = anchor.weight

        while queue:
            node = queue.popleft()
            if len(node.hops) >= self.max_hops:
                continue

            for edge, direction in self._edges_from(node):
                traversal = _TRAVERSALS.get((edge.relationship_type, direction))
                if traversal is None:
                    continue
                role, role_multiplier = traversal

                if direction == "forward":
                    next_type, next_key = edge.target_entity_type, edge.target_entity_key
                else:
                    next_type, next_key = edge.source_entity_type, edge.source_entity_key

                next_weight = node.weight * edge.weight * role_multiplier
                next_confidence = node.confidence * edge.confidence * HOP_DECAY
                if next_weight < 0.01 or next_confidence < 0.05:
                    continue

                key = (next_type.value, next_key)
                if seen.get(key, 0.0) >= next_weight:
                    continue
                seen[key] = next_weight

                is_company = next_type == EntityType.COMPANY
                hop = Hop(
                    from_type=node.entity_type.value,
                    from_key=node.entity_key,
                    relationship=edge.relationship_type.value,
                    to_type=next_type.value,
                    to_key=next_key,
                    direction=direction,
                    weight=edge.weight,
                    confidence=edge.confidence,
                )
                next_node = _Node(
                    entity_type=next_type,
                    entity_key=next_key,
                    weight=next_weight,
                    confidence=next_confidence,
                    # Order of effect counts company hops: the first company reached is a
                    # first-order beneficiary, its supplier is second-order, and so on.
                    order=node.order + (1 if is_company else 0),
                    hops=[*node.hops, hop],
                    role=role if is_company else node.role,
                    modes=[*node.modes, edge.data_mode],
                )
                queue.append(next_node)

                if is_company and next_key in company_keys:
                    path = ExposurePath(
                        company_key=next_key,
                        role=role,
                        order_of_effect=max(1, min(5, next_node.order)),
                        weight=round(next_weight, 6),
                        confidence=round(next_confidence, 6),
                        hops=next_node.hops,
                        data_mode=DataMode.weakest(next_node.modes),
                    )
                    current = best.get(next_key)
                    if current is None or path.weight > current.weight:
                        best[next_key] = path

        return sorted(best.values(), key=lambda p: -p.weight)

    def _edges_from(self, node: _Node) -> list[tuple[EntityRelationship, str]]:
        key = (node.entity_type.value, node.entity_key)
        edges: list[tuple[EntityRelationship, str]] = [
            (edge, "forward") for edge in self._outgoing.get(key, [])
        ]
        edges.extend((edge, "reverse") for edge in self._incoming.get(key, []))
        return edges
