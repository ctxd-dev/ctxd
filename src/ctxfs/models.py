from __future__ import annotations

from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _CtxfsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocClass(str, Enum):
    APPEND = "append"
    EDITABLE = "editable"
    IMMUTABLE = "immutable"


class OperationKind(str, Enum):
    PUT = "put"
    MOVE = "move"
    DELETE = "delete"
    DECISION = "decision"


class SubmissionState(str, Enum):
    QUEUED = "queued"
    APPLIED = "applied"
    FAILED = "failed"
    DISCARDED = "discarded"


class DecisionState(str, Enum):
    UPLOAD_REQUIRED = "upload_required"
    STORED = "stored"
    ABSENT = "absent"
    SKIPPED = "skipped"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"


class Provenance(_CtxfsModel):
    producer: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class PutOperation(_CtxfsModel):
    kind: Literal[OperationKind.PUT] = OperationKind.PUT
    path: str = Field(min_length=1)
    source_key: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    doc_class: DocClass
    file_format: str = Field(min_length=1)
    text: str
    provenance: Provenance


class MoveOperation(_CtxfsModel):
    kind: Literal[OperationKind.MOVE] = OperationKind.MOVE
    source_key: str = Field(min_length=1)
    path: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    expected_content_hash: str = Field(min_length=1)

    @field_validator("expected_content_hash")
    @classmethod
    def validate_expected_content_hash(cls, value: str) -> str:
        return _validate_sha256_content_hash(value)


class DeleteOperation(_CtxfsModel):
    kind: Literal[OperationKind.DELETE] = OperationKind.DELETE
    source_key: str = Field(min_length=1)
    source_version: str = Field(min_length=1)


class DecisionOperation(_CtxfsModel):
    kind: Literal[OperationKind.DECISION] = OperationKind.DECISION
    source_key: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    state: DecisionState
    path: str | None = Field(default=None, min_length=1)
    failure_code: str | None = Field(default=None, min_length=1)
    failure_message: str | None = Field(default=None, min_length=1)
    retry_after_seconds: int | None = Field(default=None, ge=0)


CtxfsOperation = PutOperation | MoveOperation | DeleteOperation | DecisionOperation


class Entry(_CtxfsModel):
    path: str
    source_key: str
    source_version: str
    doc_class: DocClass
    file_format: str
    content_hash: str
    size_bytes: int = Field(ge=0)
    line_count: int = Field(ge=0)
    object_id: str = Field(min_length=1)
    provenance: Provenance
    created_at: float
    updated_at: float

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _validate_sha256_content_hash(value)


def _validate_sha256_content_hash(value: str) -> str:
    if not value.startswith("sha256:") or len(value) != 71:
        raise ValueError("content_hash must be sha256:<64 hex>")
    int(value.removeprefix("sha256:"), 16)
    return value


class Submission(_CtxfsModel):
    submission_id: str
    state: SubmissionState
    operation_count: int = Field(ge=0)
    error: str | None = None


class Decision(_CtxfsModel):
    source_key: str
    source_version: str
    state: DecisionState
    path: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0)
    created_at: float
    updated_at: float


class WriterStatusSummary(_CtxfsModel):
    submission_state_counts: dict[SubmissionState, int]
    decision_state_counts: dict[DecisionState, int] = Field(default_factory=dict)
    entry_count: int = Field(ge=0)
    incomplete_entry_count: int = Field(ge=0)
    queue_depth: int = Field(ge=0)
    oldest_unapplied_submission_age_seconds: int | None = Field(default=None, ge=0)
    latest_submission_updated_at: float | None = Field(default=None, gt=0)


class DeletionResult(_CtxfsModel):
    scope: Literal["prefix", "integration", "user"]
    user_id: str
    writer_id: str | None = None
    prefix: str | None = None
    receipt: str | None = None
    entry_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    queued_submission_count: int = Field(ge=0)
    writer_registration_count: int = Field(ge=0)


T = TypeVar("T")


class Bounded(_CtxfsModel, Generic[T]):
    items: list[T]
    complete: bool
    stopped_by: str | None = None


class DirectoryEntry(_CtxfsModel):
    path: str
    kind: Literal["file", "directory"]
    size_bytes: int | None = Field(default=None, ge=0)
    line_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None


class ReadResult(_CtxfsModel):
    path: str
    text: str
    content_hash: str
    complete: bool
    stopped_by: str | None = None


class ReadLinesResult(_CtxfsModel):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=0)
    lines: list[str]
    content_hash: str
    complete: bool
    stopped_by: str | None = None


class GrepMatch(_CtxfsModel):
    path: str
    line_number: int = Field(ge=1)
    line: str
    match_start: int = Field(ge=0)
    match_end: int = Field(ge=0)
    content_hash: str
