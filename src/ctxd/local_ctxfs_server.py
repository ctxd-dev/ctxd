from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ctxfs import (
    Bounded,
    CtxfsOperation,
    CtxfsStore,
    DecisionOperation,
    DeleteOperation,
    DirectoryEntry,
    Entry,
    GrepMatch,
    MoveOperation,
    PutOperation,
    ReadLinesResult,
    ReadResult,
    Submission,
)
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

LOCAL_CTXFS_USER_ID = "local"
DEFAULT_CTXFS_HOST = "127.0.0.1"
DEFAULT_CTXFS_PORT = 8765
T = TypeVar("T")


class CtxfsSubmissionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    is_full_scan: bool
    operations: list[dict[str, Any]]


class CtxfsSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    writer_id: str = Field(min_length=1)
    writer_prefix: str = Field(min_length=1)
    submission: CtxfsSubmissionBody


def default_ctxfs_root(local_home: Path | None = None) -> Path:
    if local_home is None:
        local_home = Path.home() / ".ctxd" / "local"
    return local_home.expanduser().parent / "ctxfs"


def create_ctxfs_app(root: Path) -> FastAPI:
    app = FastAPI(title="ctxd local ctxfs")

    @app.get("/health")
    def health() -> dict[str, Any]:
        store_root = _with_store(root, lambda store: str(store.root))
        return {"ok": True, "root": store_root}

    @app.get("/api/ctxfs/stat", response_model=Entry | None)
    def stat_ctxfs_path(
        path: str = Query(..., min_length=1),
        _identity_guard: None = Depends(_reject_user_id_query),
    ) -> Entry | None:
        return _with_store(
            root,
            lambda store: _call_store(store.stat, LOCAL_CTXFS_USER_ID, path),
        )

    @app.get("/api/ctxfs/ls", response_model=Bounded[DirectoryEntry])
    def list_ctxfs_path(
        path: str = "",
        _identity_guard: None = Depends(_reject_user_id_query),
        limit: int = Query(1000, ge=1, le=5000),
    ) -> Bounded[DirectoryEntry]:
        return _with_store(
            root,
            lambda store: _call_store(
                store.ls,
                LOCAL_CTXFS_USER_ID,
                path,
                limit=limit,
            ),
        )

    @app.get("/api/ctxfs/tree", response_model=Bounded[DirectoryEntry])
    def tree_ctxfs_path(
        prefix: str = "",
        _identity_guard: None = Depends(_reject_user_id_query),
        depth: int | None = Query(default=None, ge=0),
        limit: int = Query(1000, ge=1, le=5000),
    ) -> Bounded[DirectoryEntry]:
        return _with_store(
            root,
            lambda store: _call_store(
                store.tree,
                LOCAL_CTXFS_USER_ID,
                prefix,
                depth=depth,
                limit=limit,
            ),
        )

    @app.get("/api/ctxfs/glob", response_model=Bounded[DirectoryEntry])
    def glob_ctxfs_paths(
        pattern: str = Query(..., min_length=1),
        prefix: str = "",
        _identity_guard: None = Depends(_reject_user_id_query),
        limit: int = Query(1000, ge=1, le=5000),
    ) -> Bounded[DirectoryEntry]:
        return _with_store(
            root,
            lambda store: _call_store(
                store.glob,
                LOCAL_CTXFS_USER_ID,
                pattern,
                prefix=prefix,
                limit=limit,
            ),
        )

    @app.get("/api/ctxfs/read", response_model=ReadResult)
    def read_ctxfs_path(
        path: str = Query(..., min_length=1),
        _identity_guard: None = Depends(_reject_user_id_query),
        max_bytes: int = Query(1_000_000, ge=1, le=5_000_000),
    ) -> ReadResult:
        return _with_store(
            root,
            lambda store: _call_store(
                store.read,
                LOCAL_CTXFS_USER_ID,
                path,
                max_bytes=max_bytes,
            ),
        )

    @app.get("/api/ctxfs/read-lines", response_model=ReadLinesResult)
    def read_ctxfs_lines(
        path: str = Query(..., min_length=1),
        start_line: int = Query(..., ge=1),
        end_line: int = Query(..., ge=1),
        _identity_guard: None = Depends(_reject_user_id_query),
    ) -> ReadLinesResult:
        return _with_store(
            root,
            lambda store: _call_store(
                store.read_lines,
                LOCAL_CTXFS_USER_ID,
                path,
                start_line,
                end_line,
            ),
        )

    @app.get("/api/ctxfs/grep", response_model=Bounded[GrepMatch])
    def grep_ctxfs_paths(
        pattern: str = Query(..., min_length=1),
        prefix: str = "",
        _identity_guard: None = Depends(_reject_user_id_query),
        limit: int = Query(1000, ge=1, le=5000),
    ) -> Bounded[GrepMatch]:
        try:
            return _with_store(
                root,
                lambda store: _call_store(
                    store.grep,
                    LOCAL_CTXFS_USER_ID,
                    pattern,
                    prefix=prefix,
                    limit=limit,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/ctxfs/submissions", response_model=Submission)
    def submit_ctxfs_operations(
        payload: CtxfsSubmissionRequest,
        _identity_guard: None = Depends(_reject_user_id_query),
    ) -> Submission:
        def submit(store: CtxfsStore) -> Submission:
            store.register_prefix(
                LOCAL_CTXFS_USER_ID,
                payload.writer_id,
                payload.writer_prefix,
            )
            operations = [
                _operation_from_payload(operation)
                for operation in payload.submission.operations
            ]
            return store.submit(LOCAL_CTXFS_USER_ID, payload.writer_id, operations)

        return _with_store(root, submit)

    return app


def _with_store(root: Path, operation: Callable[[CtxfsStore], T]) -> T:
    store = CtxfsStore(root)
    try:
        return operation(store)
    finally:
        store.close()


def _call_store(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return operation(*args, **kwargs)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _reject_user_id_query(user_id: str | None = Query(default=None)) -> None:
    if user_id is not None:
        raise HTTPException(
            status_code=400,
            detail="ctxfs local user identity is derived by the service",
        )


def _operation_from_payload(payload: dict[str, Any]) -> CtxfsOperation:
    kind = payload.get("kind")
    if kind == "put":
        return PutOperation.model_validate(payload)
    if kind == "move":
        return MoveOperation.model_validate(payload)
    if kind == "delete":
        return DeleteOperation.model_validate(payload)
    if kind == "decision":
        return DecisionOperation.model_validate(payload)
    raise ValueError(f"unsupported ctxfs operation kind: {kind!r}")
