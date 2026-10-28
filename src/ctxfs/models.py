from __future__ import annotations

from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class MachineState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class FolderState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    MISSING = "missing"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class Machine(_CtxfsModel):
    machine_id: str = Field(min_length=1, max_length=128)
    machine_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    state: MachineState
    platform: str | None = Field(default=None, min_length=1, max_length=128)
    ctxd_version: str | None = Field(default=None, min_length=1, max_length=128)
    credential_key_id: str | None = Field(default=None, min_length=1, max_length=128)
    registered_at: float = Field(gt=0, allow_inf_nan=False)

    @field_validator(
        "machine_id",
        "machine_key",
        "name",
        "platform",
        "ctxd_version",
        "credential_key_id",
    )
    @classmethod
    def validate_catalog_text(cls, value: str | None) -> str | None:
        if value is not None:
            if not value.strip():
                raise ValueError("must be non-empty after trimming whitespace")
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("cannot contain control characters")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("cannot contain control characters")
        return value


class TrackedFolder(_CtxfsModel):
    folder_id: str = Field(min_length=1)
    machine_id: str = Field(min_length=1)
    folder_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    state: FolderState
    local_registration_id: str = Field(min_length=1)
    registered_at: float = Field(gt=0)


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


class GrepPage(_CtxfsModel):
    items: list[GrepMatch]
    complete: bool
    stopped_by: str | None = None
    next_cursor: str | None = None
    continuation_required: bool = False
    continuation_hint: str | None = None

    @model_validator(mode="after")
    def set_continuation_action(self) -> "GrepPage":
        if self.complete and self.next_cursor is not None:
            raise ValueError("complete grep page cannot include a continuation cursor")
        self.continuation_required = self.next_cursor is not None
        if self.next_cursor is None:
            self.continuation_hint = None
        elif self.stopped_by == "timeout":
            self.continuation_hint = (
                "Search timed out before completion. Repeat grep with the same "
                "pattern and prefix, passing next_cursor as cursor. If the same "
                "page times out again, increase timeout_seconds or narrow prefix."
            )
        else:
            self.continuation_hint = (
                "More matches may exist. Repeat grep with the same pattern and "
                "prefix, passing next_cursor as cursor."
            )
        return self
