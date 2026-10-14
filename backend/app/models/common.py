from datetime import datetime, timezone

from sqlalchemy import JSON, Enum
from sqlalchemy.dialects.postgresql import JSONB

JSON_DATA = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_type(cls):
    return Enum(cls, name=cls.__name__.lower(), native_enum=False, create_constraint=True)
