"""Shared SQLAlchemy column type definitions faithful to PostgreSQL 16 with SQLite variants."""

from __future__ import annotations

from sqlalchemy import CHAR, JSON, DateTime, String, Uuid
from sqlalchemy.dialects.postgresql import INET, JSONB

UUIDType = Uuid(as_uuid=True)
JSONBType = JSONB().with_variant(JSON(), "sqlite")
INETType = INET().with_variant(String(45), "sqlite")
TIMESTAMPTZ = DateTime(timezone=True)
CHAR3 = CHAR(3)
CHAR64 = CHAR(64)
