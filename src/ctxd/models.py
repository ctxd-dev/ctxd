from typing import Any, Generic, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

T = TypeVar("T")


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(validation_alias=AliasChoices("id", "document_uid"))
    app_name: str | None = None
    title: str
    url: str
    text: str = Field(
        default="",
        validation_alias=AliasChoices("text", "snippet"),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    results: list[SearchItem] = Field(default_factory=list)
    error: str | None = None
    dsl_parse_error: str | None = None


class DocumentResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    app_name: str | None = None
    title: str | None = None
    text: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ProfileResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    integration_access: str
    file_tree: str


class CtxfsStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str | None = None
    endpoint: str | None = None


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
    created_at: str | None = None
    updated_at: str | None = None


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
