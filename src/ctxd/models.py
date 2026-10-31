from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from ctxfs.models import Machine as CtxfsMachine

T = TypeVar("T")


class CtxfsStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str | None = None
    endpoint: str | None = None


class CtxfsMachineRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    machine: CtxfsMachine
    key_id: str = Field(min_length=1)
    token: str = Field(min_length=1)
    recovered: bool = False


class CtxfsEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    kind: str | None = None
    source_key: str | None = None
    source_version: str | None = None
    doc_class: str | None = None
    file_format: str | None = None
    content_hash: str | None = None
    size: int | None = None
    line_count: int | None = None
    object_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: float | None = None
    updated_at: float | None = None


class CtxfsDirectoryEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    kind: str | None = None
    size_bytes: int | None = None
    line_count: int | None = None
    content_hash: str | None = None


class CtxfsBounded(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="allow")

    items: list[T] = Field(default_factory=list)
    complete: bool = True
    stopped_by: str | None = None


class CtxfsReadResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    text: str
    content_hash: str | None = None
    complete: bool = True
    stopped_by: str | None = None


class CtxfsReadLinesResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    start_line: int
    end_line: int
    lines: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    complete: bool = True
    stopped_by: str | None = None


class CtxfsGrepMatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    line_number: int
    line: str
    match_start: int | None = None
    match_end: int | None = None
    content_hash: str | None = None


class CtxfsGrepPage(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[CtxfsGrepMatch] = Field(default_factory=list)
    complete: bool = True
    stopped_by: str | None = None
    next_cursor: str | None = None
    continuation_required: bool = False
    continuation_hint: str | None = None


class CtxfsGrepResult(BaseModel):
    """Agent-facing grep result without transport pagination details."""

    items: list[CtxfsGrepMatch] = Field(default_factory=list)
    complete: bool = True
    stopped_by: str | None = None
