"""In-process domain event bus.

Cycle 1 dispatches synchronously (ADR-011). The value here is the *contract*: these are the
events a queue-backed worker pool would consume, so moving to one is a change of dispatcher
rather than a change of architecture.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from marketradar.logging import get_logger

log = get_logger(__name__)


class DomainEvent(StrEnum):
    SOURCE_INGESTED = "SOURCE_INGESTED"
    EVIDENCE_EXTRACTED = "EVIDENCE_EXTRACTED"
    EVENT_CREATED = "EVENT_CREATED"
    SIGNAL_UPDATED = "SIGNAL_UPDATED"
    TREND_UPDATED = "TREND_UPDATED"
    THEME_UPDATED = "THEME_UPDATED"
    RESEARCH_REQUESTED = "RESEARCH_REQUESTED"
    RESEARCH_COMPLETED = "RESEARCH_COMPLETED"


@dataclass
class EventBus:
    """Minimal synchronous pub/sub. A failing subscriber never breaks the publisher."""

    _subscribers: dict[DomainEvent, list[Callable[[dict[str, Any]], None]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def subscribe(
        self, event: DomainEvent, handler: Callable[[dict[str, Any]], None]
    ) -> None:
        self._subscribers[event].append(handler)

    def publish(self, event: DomainEvent, payload: dict[str, Any] | None = None) -> None:
        data = payload or {}
        log.debug("bus.publish", domain_event=event.value, **data)
        for handler in self._subscribers.get(event, []):
            try:
                handler(data)
            except Exception as exc:  # noqa: BLE001 - a subscriber must not break the pipeline
                log.error(
                    "bus.handler_failed",
                    domain_event=event.value,
                    handler=getattr(handler, "__name__", repr(handler)),
                    error=str(exc),
                )


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus
