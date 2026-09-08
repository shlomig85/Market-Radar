"""Value-chain traversal semantics, exercised without a database."""

from __future__ import annotations

from dataclasses import dataclass

from marketradar.domain.enums import DataMode, EntityType, ExposureRole, RelationshipType
from marketradar.mapping.value_chain import Anchor, ValueChainTraverser


@dataclass
class FakeEdge:
    source_entity_type: EntityType
    source_entity_key: str
    target_entity_type: EntityType
    target_entity_key: str
    relationship_type: RelationshipType
    weight: float = 0.9
    confidence: float = 0.9
    data_mode: DataMode = DataMode.DEMO


class FakeSession:
    """Minimal stand-in: the traverser only reads edges and company keys."""

    def __init__(self, edges, company_keys):
        self._edges = edges
        self._company_keys = company_keys

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        name = entity.__name__ if entity else ""
        rows = self._edges if name == "EntityRelationship" else [
            type("C", (), {"key": key})() for key in self._company_keys
        ]
        return type("R", (), {"all": lambda _self: rows})()


def _traverser(edges, companies):
    return ValueChainTraverser(FakeSession(edges, companies))


CHAIN = [
    FakeEdge(EntityType.TECHNOLOGY, "driver", EntityType.PRODUCT, "widget",
             RelationshipType.DRIVES_DEMAND_FOR),
    FakeEdge(EntityType.COMPANY, "maker", EntityType.PRODUCT, "widget",
             RelationshipType.PRODUCES),
    FakeEdge(EntityType.COMPANY, "toolco", EntityType.COMPANY, "maker",
             RelationshipType.SUPPLIES),
    FakeEdge(EntityType.COMPANY, "materialco", EntityType.COMPANY, "toolco",
             RelationshipType.SUPPLIES),
]
ANCHOR = [Anchor(EntityType.TECHNOLOGY, "driver", 1.0)]


def test_producer_of_a_demanded_product_is_a_first_order_beneficiary():
    paths = _traverser(CHAIN, {"maker", "toolco", "materialco"}).traverse(ANCHOR)
    maker = next(p for p in paths if p.company_key == "maker")
    assert maker.role == ExposureRole.DIRECT_BENEFICIARY
    assert maker.order_of_effect == 1


def test_supplier_chain_produces_second_and_third_order_exposure():
    traverser = _traverser(CHAIN, {"maker", "toolco", "materialco"})
    paths = {p.company_key: p for p in traverser.traverse(ANCHOR)}
    assert paths["toolco"].order_of_effect == 2
    assert paths["toolco"].role == ExposureRole.SUPPLIER
    assert paths["materialco"].order_of_effect == 3


def test_exposure_and_confidence_decay_with_distance():
    traverser = _traverser(CHAIN, {"maker", "toolco", "materialco"})
    paths = {p.company_key: p for p in traverser.traverse(ANCHOR)}
    assert paths["maker"].exposure_score > paths["toolco"].exposure_score
    assert paths["toolco"].exposure_score > paths["materialco"].exposure_score
    assert paths["maker"].confidence > paths["materialco"].confidence


def test_the_path_is_recorded_and_readable():
    paths = _traverser(CHAIN, {"maker"}).traverse(ANCHOR)
    described = paths[0].describe()
    assert "DRIVES_DEMAND_FOR" in described and "PRODUCES" in described
    assert len(paths[0].hops) == 2


def test_cycles_do_not_hang_the_traversal():
    cyclic = CHAIN + [
        FakeEdge(EntityType.COMPANY, "maker", EntityType.COMPANY, "rival",
                 RelationshipType.COMPETES_WITH),
        FakeEdge(EntityType.COMPANY, "rival", EntityType.COMPANY, "maker",
                 RelationshipType.COMPETES_WITH),
    ]
    paths = _traverser(cyclic, {"maker", "rival"}).traverse(ANCHOR)
    assert {p.company_key for p in paths} == {"maker", "rival"}
    assert next(p for p in paths if p.company_key == "rival").role == ExposureRole.COMPETITOR


def test_traversal_stops_at_the_hop_limit():
    long_chain = [
        FakeEdge(EntityType.TECHNOLOGY, "driver", EntityType.PRODUCT, "widget",
                 RelationshipType.DRIVES_DEMAND_FOR)
    ]
    names = ["c0", "c1", "c2", "c3", "c4", "c5", "c6"]
    long_chain.append(
        FakeEdge(EntityType.COMPANY, "c0", EntityType.PRODUCT, "widget",
                 RelationshipType.PRODUCES)
    )
    for i in range(1, len(names)):
        long_chain.append(
            FakeEdge(EntityType.COMPANY, names[i], EntityType.COMPANY, names[i - 1],
                     RelationshipType.SUPPLIES)
        )
    paths = _traverser(long_chain, set(names)).traverse(ANCHOR)
    assert max(len(p.hops) for p in paths) <= 4
    assert len(paths) < len(names), "the hop limit must actually bind"


def test_unreachable_companies_are_absent():
    paths = _traverser(CHAIN, {"maker", "unrelated"}).traverse(ANCHOR)
    assert "unrelated" not in {p.company_key for p in paths}
