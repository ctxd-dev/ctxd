from __future__ import annotations

from collections.abc import Callable
from enum import Enum
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
    LocalSocketTrust,
    Principal,
    Scope,
    authorize,
    local_socket_principal,
)
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ctxfs.credentials import CredentialStore

from ctxd.mcp_server import handle_message

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

    # These fields remain transport metadata for compatibility with existing
    # trackers.  They never select the authenticated principal or its grants.
    user_id: str | None = None
    writer_id: str = Field(min_length=1)
    writer_prefix: str = Field(min_length=1)
    submission: CtxfsSubmissionBody


class CtxfsAuthMode(str, Enum):
    """Explicit authentication policy selected by the server transport."""

    TRUSTED_LOCAL = "trusted_local"
    BEARER = "bearer"


def default_ctxfs_root(local_home: Path | None = None) -> Path:
    if local_home is None:
        local_home = Path.home() / ".ctxd" / "local"
    return local_home.expanduser().parent / "ctxfs"


def create_ctxfs_app(
    root: Path,
    *,
    auth_mode: CtxfsAuthMode,
    credential_root: Path | None = None,
) -> FastAPI:
    if not isinstance(auth_mode, CtxfsAuthMode):
        raise ValueError("auth_mode must be a CtxfsAuthMode value")
    credential_root = root if credential_root is None else credential_root
    app = FastAPI(title="ctxd local ctxfs")

    @app.middleware("http")
    async def authenticate_request(request: Request, call_next: Callable[..., Any]):
        """Authenticate before routing or request-body validation.

        Keeping authentication at the application boundary makes bearer mode
        fail closed for generated routes, unknown paths, and malformed bodies
        as well as for the explicitly declared REST and MCP handlers.
        """
        if auth_mode is CtxfsAuthMode.TRUSTED_LOCAL:
            principal = local_socket_principal(LocalSocketTrust.TRUSTED_ADMIN)
        else:
            token = _bearer_token(request.headers.get("authorization"))
            if token is None:
                return _auth_response("missing or invalid bearer credential")
            with CredentialStore(credential_root) as credentials:
                grant = credentials.verify(token)
            principal = grant.principal() if grant is not None else None
        if principal is None:
            return _auth_response("missing or invalid bearer credential")
        request.state.ctxfs_principal = principal
        return await call_next(request)

    def require(scope: Scope) -> Callable[..., Principal]:
        def dependency(request: Request) -> Principal:
            principal: Principal = request.state.ctxfs_principal
            # Submission prefix authorization is repeated against the actual
            # server-parsed payload below. Here, establish only that the
            # principal has at least one usable submit grant (or is admin).
            authorization_path = (
                principal.prefixes[0]
                if scope is Scope.SUBMIT and principal.prefixes
                else None
            )
            decision = authorize(principal, scope, path=authorization_path)
            if not decision.allowed:
                raise _auth_error(
                    decision.http_status or 403,
                    decision.denial_reason.value
                    if decision.denial_reason
                    else "denied",
                )
            assert principal is not None
            return principal

        return dependency

    @app.get("/health")
    def health(
        _principal: Principal = Depends(require(Scope.STATUS)),
    ) -> dict[str, Any]:
        store_root = _with_store(root, lambda store: str(store.root))
        return {"ok": True, "root": store_root}

    @app.post("/mcp")
    def ctxfs_mcp(
        message: dict[str, Any],
        request: Request,
    ) -> Response:
        _authenticate_mcp(
            message,
            principal=request.state.ctxfs_principal,
        )
        response = handle_message(
            message,
            lambda name, arguments: _execute_ctxfs_tool(root, name, arguments),
        )
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    @app.get("/api/ctxfs/stat", response_model=Entry)
    def stat_ctxfs_path(
        path: str = Query(..., min_length=1),
        _principal: Principal = Depends(require(Scope.READ)),
        _identity_guard: None = Depends(_reject_user_id_query),
    ) -> Entry:
        return _with_store(
            root,
            lambda store: _stat_or_404(store, LOCAL_CTXFS_USER_ID, path),
        )

    @app.get("/api/ctxfs/ls", response_model=Bounded[DirectoryEntry])
    def list_ctxfs_path(
        path: str = "",
        _principal: Principal = Depends(require(Scope.READ)),
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
        _principal: Principal = Depends(require(Scope.READ)),
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
        _principal: Principal = Depends(require(Scope.READ)),
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
        _principal: Principal = Depends(require(Scope.READ)),
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
        _principal: Principal = Depends(require(Scope.READ)),
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
        _principal: Principal = Depends(require(Scope.READ)),
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
        principal: Principal = Depends(require(Scope.SUBMIT)),
        _identity_guard: None = Depends(_reject_user_id_query),
    ) -> Submission:
        def submit(store: CtxfsStore) -> Submission:
            try:
                try:
                    prefix_decision = authorize(
                        principal, Scope.SUBMIT, path=payload.writer_prefix
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                if not prefix_decision.allowed:
                    raise HTTPException(
                        status_code=prefix_decision.http_status or 403,
                        detail=prefix_decision.denial_reason.value,
                    )
                # A credential can own multiple disjoint roots. Derive a stable
                # server-side writer per granted root without trusting the
                # caller's writer identifier.
                granted_prefix = _submission_prefix(principal, payload.writer_prefix)
                writer_id = f"{principal.key_id or principal.principal_id}:{granted_prefix}"
                store.register_prefix(
                    LOCAL_CTXFS_USER_ID,
                    writer_id,
                    granted_prefix,
                )
                operations = [
                    _operation_from_payload(operation)
                    for operation in payload.submission.operations
                ]
                return store.submit(LOCAL_CTXFS_USER_ID, writer_id, operations)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except (ValidationError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

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


def _stat_or_404(store: CtxfsStore, user_id: str, path: str) -> Entry:
    entry = _call_store(store.stat, user_id, path)
    if entry is None:
        raise HTTPException(status_code=404, detail="ctxfs path not found")
    return entry


def _execute_ctxfs_tool(
    root: Path, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    def execute(store: CtxfsStore) -> Any:
        if name == "ls":
            return _call_store(
                store.ls,
                LOCAL_CTXFS_USER_ID,
                arguments.get("path", ""),
                limit=arguments.get("limit", 1000),
            )
        if name == "tree":
            return _call_store(
                store.tree,
                LOCAL_CTXFS_USER_ID,
                arguments.get("prefix", ""),
                depth=arguments.get("depth"),
                limit=arguments.get("limit", 1000),
            )
        if name == "glob":
            return _call_store(
                store.glob,
                LOCAL_CTXFS_USER_ID,
                arguments["pattern"],
                prefix=arguments.get("prefix", ""),
                limit=arguments.get("limit", 1000),
            )
        if name == "stat":
            return _stat_or_404(store, LOCAL_CTXFS_USER_ID, arguments["path"])
        if name == "read":
            return _call_store(
                store.read,
                LOCAL_CTXFS_USER_ID,
                arguments["path"],
                max_bytes=arguments.get("max_bytes", 1_000_000),
            )
        if name == "read_lines":
            return _call_store(
                store.read_lines,
                LOCAL_CTXFS_USER_ID,
                arguments["path"],
                arguments["start_line"],
                arguments["end_line"],
            )
        if name == "grep":
            return _call_store(
                store.grep,
                LOCAL_CTXFS_USER_ID,
                arguments["pattern"],
                prefix=arguments.get("prefix", ""),
                limit=arguments.get("limit", 1000),
            )
        raise ValueError(f"Unknown ctxfs tool: {name}")

    value = _with_store(root, execute)
    return value.model_dump(mode="json")


def _reject_user_id_query(user_id: str | None = Query(default=None)) -> None:
    if user_id is not None:
        raise HTTPException(
            status_code=400,
            detail="ctxfs local user identity is derived by the service",
        )


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    if token.strip() != token or " " in token:
        return None
    return token


def _auth_error(status_code: int, detail: str) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def _auth_response(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _authenticate_mcp(
    message: dict[str, Any],
    *,
    principal: Principal,
) -> Principal:
    method = message.get("method")
    required_scope = (
        Scope.STATUS
        if method in {"initialize", "ping", "notifications/initialized"}
        else Scope.READ
    )
    decision = authorize(principal, required_scope)
    if not decision.allowed:
        raise _auth_error(
            decision.http_status or 403,
            decision.denial_reason.value if decision.denial_reason else "denied",
        )
    assert principal is not None
    return principal


def _submission_prefix(principal: Principal, claimed_prefix: str) -> str:
    """Resolve a request claim to the most-specific server-owned prefix grant."""
    if Scope.ADMIN in principal.scopes:
        return claimed_prefix
    matches = [
        prefix
        for prefix in principal.prefixes
        if claimed_prefix == prefix or claimed_prefix.startswith(f"{prefix}/")
    ]
    # Authorization has already established that at least one match exists.
    return max(matches, key=len)


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
