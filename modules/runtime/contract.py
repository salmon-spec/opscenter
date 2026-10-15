from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModuleInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    version: str
    capabilities: list[str] = Field(default_factory=list)


class ModuleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "unavailable"]
    collected_at: datetime | None = None
    stale_after_seconds: int = Field(default=120, ge=1, le=86400)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    resource_id: str | None = Field(default=None, max_length=160)
    severity: Literal["info", "warning", "critical"]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=1000)
    observed_at: datetime
    url: str | None = Field(default=None, max_length=2000)


class FindingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "unavailable"]
    collected_at: datetime | None = None
    items: list[Finding] = Field(default_factory=list, max_length=1000)
    error: str | None = None
