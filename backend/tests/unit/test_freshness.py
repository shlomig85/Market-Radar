"""Timestamp discipline: publication is not occurrence (ADR-013)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from marketradar.domain.enums import DataMode
from marketradar.providers.base import ProviderDocument

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _document(published, event=None):
    return ProviderDocument(
        external_id="d1",
        source_key="s1",
        url="https://example.invalid/a",
        title="t",
        body_text="Demand is accelerating.",
        published_at=published,
        event_at=event,
        data_mode=DataMode.DEMO,
    )


def test_event_time_is_kept_separate_from_publication_time():
    old_event = NOW - timedelta(days=900)
    document = _document(published=NOW, event=old_event)
    assert document.published_at == NOW
    assert document.event_at == old_event


def test_a_missing_event_time_is_left_none_for_ingestion_to_flag():
    """The provider must not silently pretend publication is occurrence — ingestion
    records the inference and sets ``event_at_inferred``."""
    assert _document(published=NOW).event_at is None


def test_signal_recency_would_use_event_time_not_publication():
    """A 2026 article about a 2024 event must not read as new evidence."""
    document = _document(published=NOW, event=NOW - timedelta(days=700))
    observation_start = NOW - timedelta(days=30)
    assert document.published_at >= observation_start
    assert document.event_at < observation_start
