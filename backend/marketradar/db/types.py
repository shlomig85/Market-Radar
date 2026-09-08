"""Shared column types."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

# JSONB is used only where the payload shape is legitimately open-ended:
# provider snapshots, agent I/O envelopes, score input bags. Anything queried or
# filtered is a real column with an index.
JsonDict = JSONB


class StrEnumText(TypeDecorator[Any]):
    """Persist a ``StrEnum`` as its text value.

    Native PG enums are avoided deliberately: adding a signal category or event type
    would otherwise require a migration with an ``ALTER TYPE``, and these vocabularies
    are expected to grow. Validity is enforced in the domain layer and by check
    constraints where a value set is genuinely closed.
    """

    impl = Text
    cache_ok = True

    def __init__(self, enum_cls: Any) -> None:
        super().__init__()
        self.enum_cls = enum_cls

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_cls):
            return str(value.value)
        return str(self.enum_cls(value).value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self.enum_cls(value)
