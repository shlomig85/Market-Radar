"""Entity resolution: mapping text mentions to companies."""

from marketradar.entities.resolver import (
    CompanyRecord,
    EntityMatch,
    EntityResolver,
    SurfaceForm,
)

__all__ = ["CompanyRecord", "EntityMatch", "EntityResolver", "SurfaceForm"]
