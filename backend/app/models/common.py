"""Shared request/response primitives."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    """Base for domain models: immutable, strict, no silent extra fields.

    ``extra="forbid"`` matters more than it looks. Without it, a typo in a field
    name is silently accepted and the value is lost. With it, the typo is a 422.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class PageParams(BaseModel):
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HealthStatus(BaseModel):
    status: str
    environment: str
    version: str
    checked_at: datetime
    dependencies: dict[str, str]
