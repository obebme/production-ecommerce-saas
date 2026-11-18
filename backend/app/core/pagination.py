"""Shared pagination primitives and safe ILIKE query escaping."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    """Standard pagination query bounds matching OpenAPI contracts."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse[T](BaseModel):
    """Standard paginated response envelope."""

    items: list[T]
    total: int
    page: int
    page_size: int
    has_next: bool


def escape_like_query(raw: str) -> str:
    """Escape LIKE/ILIKE wildcard characters (\\, %, _) safely."""
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
