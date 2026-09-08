"""Entity resolution and relationship extraction: text mentions -> graph nodes and edges."""

from marketradar.entities.relationships import (
    RELATIONSHIP_EXTRACTOR_NAME,
    RELATIONSHIP_EXTRACTOR_VERSION,
    ExtractedRelationship,
    RelationshipRule,
    extract_relationships,
)
from marketradar.entities.resolver import (
    CompanyRecord,
    EntityMatch,
    EntityResolver,
    SurfaceForm,
)

__all__ = [
    "RELATIONSHIP_EXTRACTOR_NAME",
    "RELATIONSHIP_EXTRACTOR_VERSION",
    "CompanyRecord",
    "EntityMatch",
    "EntityResolver",
    "ExtractedRelationship",
    "RelationshipRule",
    "SurfaceForm",
    "extract_relationships",
]
