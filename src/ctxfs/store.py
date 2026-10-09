from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import logging
import os
import re
import selectors
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from ctxfs.models import (
    Bounded,
    CtxfsOperation,
    DecisionOperation,
    DecisionState,
    DeleteOperation,
    DirectoryEntry,
    Entry,
    GrepMatch,
    MoveOperation,
    OperationKind,
    Provenance,
    PutOperation,
    ReadLinesResult,
    ReadResult,
    Submission,
    SubmissionState,
    WriterStatusSummary,
)

logger = logging.getLogger(__name__)

FilesystemCallback = Callable[[], None]
SourceIdentity = tuple[str, str]
_ASCII_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
GREP_TIMEOUT_SECONDS = 2.0
GREP_JSON_CHUNK_BYTES = 64 * 1024
GREP_MAX_JSON_RECORD_BYTES = 1_000_000


@dataclass
class _GrepPathResult:
    matches: list[GrepMatch]
    stopped_by: str | None = None


class PrefixAuthorizationError(PermissionError):
    pass


class ContentHashMismatchError(ValueError):
    pass


class CtxfsStore:
    def __init__(self, root: Path):
        self.root = root
        self.catalog_path = root / "catalog.sqlite3"
        self.tree_root = root / "users"
        self.objects_root = root / "objects"
        self.undo_root = root / "undo"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tree_root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self.undo_root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.catalog_path)
        self.connection.row_factory = sqlite3.Row
        self.apply_migrations()

    def close(self) -> None:
        self.connection.close()

    def apply_migrations(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS writer_prefixes (
                    user_id TEXT NOT NULL,
                    writer_id TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(user_id, writer_id, prefix)
                )
                """
            )
            self._migrate_entries_table()
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    writer_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    operation_count INTEGER NOT NULL,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    user_id TEXT NOT NULL,
                    writer_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    path TEXT,
                    state TEXT NOT NULL,
                    failure_code TEXT,
                    failure_message TEXT,
                    retry_after_seconds INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, writer_id, source_key)
                )
                """
            )

    def register_prefix(self, user_id: str, writer_id: str, prefix: str) -> None:
        normalized = _normalize_path(prefix)
        now = time.time()
        with self.connection:
            rows = self.connection.execute(
                """
                SELECT writer_id, prefix FROM writer_prefixes
                WHERE user_id = ? AND writer_id != ?
                """,
                (user_id, writer_id),
            ).fetchall()
            for row in rows:
                existing_prefix = row["prefix"]
                if _prefixes_overlap(normalized, existing_prefix):
                    raise PrefixAuthorizationError(
                        f"writer {writer_id} prefix {normalized} overlaps "
                        f"writer {row['writer_id']} prefix {existing_prefix}"
                    )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO writer_prefixes(
                    user_id, writer_id, prefix, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (user_id, writer_id, normalized, now),
            )

    def submit(
        self,
        user_id: str,
        writer_id: str,
        operations: list[CtxfsOperation],
    ) -> Submission:
        submission_id = str(uuid.uuid4())
        now = time.time()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO submissions(
                    submission_id, user_id, writer_id, state, operation_count,
                    error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    submission_id,
                    user_id,
                    writer_id,
                    SubmissionState.QUEUED.value,
                    len(operations),
                    now,
                    now,
                ),
            )
        try:
            undo_stack: list[FilesystemCallback] = []
            cleanup_stack: list[FilesystemCallback] = []
            self._reject_duplicate_move_sources(writer_id, operations)
            ordered_operations = self._ordered_operations(
                user_id, writer_id, operations
            )
            self._preflight_submission(user_id, writer_id, ordered_operations)
            with self.connection:
                index = 0
                while index < len(ordered_operations):
                    operation = ordered_operations[index]
                    if operation.kind == OperationKind.PUT:
                        self._put(
                            user_id,
                            writer_id,
                            operation,
                            undo_stack,
                            cleanup_stack,
                        )
                        index += 1
                    elif operation.kind == OperationKind.MOVE:
                        moves: list[MoveOperation] = []
                        while (
                            index < len(ordered_operations)
                            and ordered_operations[index].kind == OperationKind.MOVE
                        ):
                            moves.append(ordered_operations[index])
                            index += 1
                        self._move_batch(
                            user_id,
                            writer_id,
                            moves,
                            undo_stack,
                            cleanup_stack,
                        )
                    elif operation.kind == OperationKind.DELETE:
                        self._delete(
                            user_id,
                            writer_id,
                            operation,
                            undo_stack,
                            cleanup_stack,
                        )
                        index += 1
                    elif operation.kind == OperationKind.DECISION:
                        self._decision(user_id, writer_id, operation)
                        index += 1
                    else:
                        raise ValueError(f"unsupported operation: {operation.kind}")
                self.connection.execute(
                    """
                    UPDATE submissions
                    SET state = ?, updated_at = ?
                    WHERE submission_id = ?
                    """,
                    (SubmissionState.APPLIED.value, time.time(), submission_id),
                )
        except Exception as exc:
            rollback_exc = _rollback_filesystem(undo_stack)
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE submissions
                    SET state = ?, error = ?, updated_at = ?
                    WHERE submission_id = ?
                    """,
                    (
                        SubmissionState.FAILED.value,
                        str(exc) or exc.__class__.__name__,
                        time.time(),
                        submission_id,
                    ),
                )
            _cleanup_filesystem(cleanup_stack)
            if rollback_exc is not None:
                raise rollback_exc from exc
            raise
        _cleanup_filesystem(cleanup_stack)
        return self.submission(submission_id)

    def submission(self, submission_id: str) -> Submission:
        row = self.connection.execute(
            "SELECT * FROM submissions WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown submission_id: {submission_id}")
        return Submission(
            submission_id=row["submission_id"],
            state=SubmissionState(row["state"]),
            operation_count=row["operation_count"],
            error=row["error"],
        )

    def writer_status_summary(
        self,
        user_id: str,
        writer_id: str,
        *,
        prefix: str | None = None,
        now: float | None = None,
    ) -> WriterStatusSummary:
        normalized_prefix = _normalize_path(prefix) if prefix else None
        clock = time.time() if now is None else now
        rows = self.connection.execute(
            """
            SELECT state, COUNT(*) AS count
            FROM submissions
            WHERE user_id = ? AND writer_id = ?
            GROUP BY state
            """,
            (user_id, writer_id),
        ).fetchall()
        state_counts = {
            SubmissionState(state): 0
            for state in (
                SubmissionState.QUEUED.value,
                SubmissionState.APPLIED.value,
                SubmissionState.FAILED.value,
                SubmissionState.DISCARDED.value,
            )
        }
        for row in rows:
            state_counts[SubmissionState(row["state"])] = row["count"]

        oldest_unapplied = self.connection.execute(
            """
            SELECT MIN(created_at) AS created_at
            FROM submissions
            WHERE user_id = ? AND writer_id = ? AND state != ?
            """,
            (user_id, writer_id, SubmissionState.APPLIED.value),
        ).fetchone()["created_at"]
        latest_updated_at = self.connection.execute(
            """
            SELECT MAX(updated_at) AS updated_at
            FROM submissions
            WHERE user_id = ? AND writer_id = ?
            """,
            (user_id, writer_id),
        ).fetchone()["updated_at"]
        entries = list(self._entries_under_prefix(user_id, normalized_prefix or ""))
        return WriterStatusSummary(
            submission_state_counts=state_counts,
            decision_state_counts=self._decision_state_counts(
                user_id,
                writer_id,
                prefix=normalized_prefix,
            ),
            entry_count=len(entries),
            incomplete_entry_count=sum(
                1
                for entry in entries
                if not self._tree_path(user_id, entry.path).exists()
            ),
            queue_depth=state_counts[SubmissionState.QUEUED],
            oldest_unapplied_submission_age_seconds=(
                None
                if oldest_unapplied is None
                else max(0, int(clock - oldest_unapplied))
            ),
            latest_submission_updated_at=latest_updated_at,
        )

    def stat(self, user_id: str, path: str) -> Entry | None:
        normalized = _normalize_path(path)
        row = self.connection.execute(
            "SELECT * FROM entries WHERE user_id = ? AND path = ?",
            (user_id, normalized),
        ).fetchone()
        if row is None:
            return None
        entry = _entry_from_row(row)
        if not self._tree_path(user_id, entry.path).exists():
            return None
        return entry

    def ls(
        self,
        user_id: str,
        path: str,
        *,
        limit: int = 1000,
    ) -> Bounded[DirectoryEntry]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        normalized = _normalize_path(path) if path else ""
        children: dict[str, DirectoryEntry] = {}
        incomplete = False
        for entry in self._entries_under_prefix(user_id, normalized):
            if not self._tree_path(user_id, entry.path).exists():
                incomplete = True
                continue
            relative_parts = _relative_parts(normalized, entry.path)
            if not relative_parts:
                continue
            child_path = _join_relative(normalized, relative_parts[0])
            if len(relative_parts) == 1:
                children[child_path] = DirectoryEntry(
                    path=child_path,
                    kind="file",
                    size_bytes=entry.size_bytes,
                    line_count=entry.line_count,
                    content_hash=entry.content_hash,
                )
            else:
                children.setdefault(
                    child_path,
                    DirectoryEntry(path=child_path, kind="directory"),
                )
        for disk_path, tree_path in self._disk_files_under_prefix(user_id, normalized):
            if self.stat(user_id, disk_path) is not None:
                continue
            incomplete = True
            relative_parts = _relative_parts(normalized, disk_path)
            if not relative_parts:
                continue
            child_path = _join_relative(normalized, relative_parts[0])
            if len(relative_parts) == 1:
                children[child_path] = _disk_directory_entry(
                    child_path,
                    tree_path,
                )
            else:
                children.setdefault(
                    child_path,
                    DirectoryEntry(path=child_path, kind="directory"),
                )
        entries = [
            children[path]
            for path in sorted(children, key=lambda item: item.casefold())
        ]
        if len(entries) > limit:
            return Bounded(
                items=entries[:limit],
                complete=False,
                stopped_by="limit",
            )
        return Bounded(
            items=entries,
            complete=not incomplete,
            stopped_by=None if not incomplete else "catalog_mismatch",
        )

    def tree(
        self,
        user_id: str,
        prefix: str = "",
        *,
        depth: int | None = None,
        limit: int = 1000,
    ) -> Bounded[DirectoryEntry]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if depth is not None and depth < 0:
            raise ValueError("depth must be non-negative")
        normalized_prefix = _normalize_path(prefix) if prefix else ""
        entries_by_path: dict[str, DirectoryEntry] = {}
        incomplete = False
        for entry in self._entries_under_prefix(user_id, normalized_prefix):
            if not self._tree_path(user_id, entry.path).exists():
                incomplete = True
                continue
            entry_relative_depth = len(_relative_parts(normalized_prefix, entry.path))
            if depth is None or entry_relative_depth <= depth:
                entries_by_path[entry.path] = DirectoryEntry(
                    path=entry.path,
                    kind="file",
                    size_bytes=entry.size_bytes,
                    line_count=entry.line_count,
                    content_hash=entry.content_hash,
                )
            parent = PurePosixPath(entry.path).parent
            while str(parent) not in ("", "."):
                parent_path = str(parent)
                parent_relative_depth = len(
                    _relative_parts(normalized_prefix, parent_path)
                )
                if (
                    parent_path != normalized_prefix
                    and (
                        not normalized_prefix
                        or _path_is_within_prefix(parent_path, normalized_prefix)
                    )
                    and (depth is None or parent_relative_depth <= depth)
                ):
                    entries_by_path.setdefault(
                        parent_path,
                        DirectoryEntry(path=parent_path, kind="directory"),
                    )
                parent = parent.parent
        for disk_path, tree_path in self._disk_files_under_prefix(
            user_id,
            normalized_prefix,
        ):
            if self.stat(user_id, disk_path) is not None:
                continue
            incomplete = True
            entry_relative_depth = len(_relative_parts(normalized_prefix, disk_path))
            if depth is None or entry_relative_depth <= depth:
                entries_by_path[disk_path] = _disk_directory_entry(disk_path, tree_path)
            parent = PurePosixPath(disk_path).parent
            while str(parent) not in ("", "."):
                parent_path = str(parent)
                parent_relative_depth = len(
                    _relative_parts(normalized_prefix, parent_path)
                )
                if (
                    parent_path != normalized_prefix
                    and (
                        not normalized_prefix
                        or _path_is_within_prefix(parent_path, normalized_prefix)
                    )
                    and (depth is None or parent_relative_depth <= depth)
                ):
                    entries_by_path.setdefault(
                        parent_path,
                        DirectoryEntry(path=parent_path, kind="directory"),
                    )
                parent = parent.parent
        entries = [
            entries_by_path[path]
            for path in sorted(entries_by_path, key=lambda item: item.casefold())
        ]
        if len(entries) > limit:
            return Bounded(
                items=entries[:limit],
                complete=False,
                stopped_by="limit",
            )
        return Bounded(
            items=entries,
            complete=not incomplete,
            stopped_by=None if not incomplete else "catalog_mismatch",
        )

    def glob(
        self,
        user_id: str,
        pattern: str,
        *,
        prefix: str = "",
        limit: int = 1000,
    ) -> Bounded[DirectoryEntry]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        normalized_prefix = _normalize_path(prefix) if prefix else ""
        pattern_parts = _validate_glob_pattern(pattern)
        matches: list[DirectoryEntry] = []
        incomplete = False
        for entry in self._entries_under_prefix(user_id, normalized_prefix):
            if not self._tree_path(user_id, entry.path).exists():
                incomplete = True
                continue
            if not _glob_matches(pattern_parts, PurePosixPath(entry.path).parts):
                continue
            matches.append(
                DirectoryEntry(
                    path=entry.path,
                    kind="file",
                    size_bytes=entry.size_bytes,
                    line_count=entry.line_count,
                    content_hash=entry.content_hash,
                )
            )
            if len(matches) >= limit:
                return Bounded(items=matches, complete=False, stopped_by="limit")
        for disk_path, tree_path in self._disk_files_under_prefix(
            user_id,
            normalized_prefix,
        ):
            if self.stat(user_id, disk_path) is not None:
                continue
            incomplete = True
            if not _glob_matches(pattern_parts, PurePosixPath(disk_path).parts):
                continue
            matches.append(_disk_directory_entry(disk_path, tree_path))
            if len(matches) >= limit:
                return Bounded(items=matches, complete=False, stopped_by="limit")
        return Bounded(
            items=matches,
            complete=not incomplete,
            stopped_by=None if not incomplete else "catalog_mismatch",
        )

    def read(
        self, user_id: str, path: str, *, max_bytes: int = 1_000_000
    ) -> ReadResult:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        entry = self._require_entry(user_id, path)
        data = self._tree_path(user_id, entry.path).read_bytes()
        complete = len(data) <= max_bytes
        actual_hash = _sha256(data)
        if actual_hash != entry.content_hash:
            raise ContentHashMismatchError(
                f"entry {entry.path} has {actual_hash}, "
                f"expected {entry.content_hash}"
            )
        limited = data if complete else _truncate_utf8(data, max_bytes)
        return ReadResult(
            path=entry.path,
            text=limited.decode("utf-8", "replace"),
            content_hash=entry.content_hash,
            complete=complete,
            stopped_by=None if complete else "max_bytes",
        )

    def read_lines(
        self,
        user_id: str,
        path: str,
        start_line: int,
        end_line: int,
    ) -> ReadLinesResult:
        if start_line <= 0:
            raise ValueError("start_line must be positive")
        if end_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        entry = self._require_entry(user_id, path)
        data = self._tree_path(user_id, entry.path).read_bytes()
        actual_hash = _sha256(data)
        if actual_hash != entry.content_hash:
            raise ContentHashMismatchError(
                f"entry {entry.path} has {actual_hash}, "
                f"expected {entry.content_hash}"
            )
        lines = data.decode("utf-8", "replace").splitlines()
        selected = lines[start_line - 1 : end_line]
        complete = end_line <= len(lines)
        return ReadLinesResult(
            path=entry.path,
            start_line=start_line,
            end_line=min(end_line, len(lines)),
            lines=selected,
            content_hash=entry.content_hash,
            complete=complete,
            stopped_by=None if complete else "eof",
        )

    def grep(
        self,
        user_id: str,
        pattern: str,
        *,
        prefix: str = "",
        limit: int = 1000,
    ) -> Bounded[GrepMatch]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        normalized_prefix = _normalize_path(prefix) if prefix else ""
        deadline = time.monotonic() + GREP_TIMEOUT_SECONDS
        self._validate_grep_pattern(pattern)
        matches: list[GrepMatch] = []
        incomplete = False
        for entry in self._entries_under_prefix(user_id, normalized_prefix):
            tree_path = self._tree_path(user_id, entry.path)
            if not tree_path.exists():
                incomplete = True
                continue
            data = tree_path.read_bytes()
            try:
                path_result = self._grep_tree_path(
                    pattern,
                    tree_path,
                    entry.path,
                    _sha256(data),
                    deadline,
                    limit - len(matches),
                )
            except TimeoutError:
                return Bounded(items=matches, complete=False, stopped_by="timeout")
            for match in path_result.matches:
                matches.append(match)
                if len(matches) >= limit:
                    return Bounded(items=matches, complete=False, stopped_by="limit")
            if path_result.stopped_by is not None:
                return Bounded(
                    items=matches,
                    complete=False,
                    stopped_by=path_result.stopped_by,
                )
        for disk_path, tree_path in self._disk_files_under_prefix(
            user_id,
            normalized_prefix,
        ):
            if self.stat(user_id, disk_path) is not None:
                continue
            incomplete = True
            data = tree_path.read_bytes()
            try:
                path_result = self._grep_tree_path(
                    pattern,
                    tree_path,
                    disk_path,
                    _sha256(data),
                    deadline,
                    limit - len(matches),
                )
            except TimeoutError:
                return Bounded(items=matches, complete=False, stopped_by="timeout")
            for match in path_result.matches:
                matches.append(
                    GrepMatch(
                        path=disk_path,
                        line_number=match.line_number,
                        line=match.line,
                        match_start=match.match_start,
                        match_end=match.match_end,
                        content_hash=_sha256(data),
                    )
                )
                if len(matches) >= limit:
                    return Bounded(items=matches, complete=False, stopped_by="limit")
            if path_result.stopped_by is not None:
                return Bounded(
                    items=matches,
                    complete=False,
                    stopped_by=path_result.stopped_by,
                )
        return Bounded(
            items=matches,
            complete=not incomplete,
            stopped_by=None if not incomplete else "catalog_mismatch",
        )

    def _validate_grep_pattern(self, pattern: str) -> None:
        if not _has_rg():
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError("invalid ctxfs grep pattern") from exc
            return

        process = subprocess.run(
            _rg_command(pattern, Path(os.devnull)),
            capture_output=True,
            env=_rg_env(),
            text=True,
        )
        if process.returncode == 2:
            raise ValueError("invalid ctxfs grep pattern")
        if process.returncode not in {0, 1}:
            raise RuntimeError("ctxfs grep pattern validation failed")

    def _grep_tree_path(
        self,
        pattern: str,
        tree_path: Path,
        logical_path: str,
        content_hash: str,
        deadline: float,
        match_limit: int,
    ) -> _GrepPathResult:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        if not _has_rg():
            return _python_grep_tree_path(
                pattern,
                tree_path,
                logical_path,
                content_hash,
                match_limit,
            )

        process = subprocess.Popen(
            _rg_command(pattern, tree_path),
            env=_rg_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        matches: list[GrepMatch] = []
        pending = b""
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout.fileno(), selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    if not selector.select(timeout=remaining):
                        raise TimeoutError
                    chunk = os.read(process.stdout.fileno(), GREP_JSON_CHUNK_BYTES)
                    if chunk == b"":
                        break
                    pending += chunk
                    while b"\n" in pending:
                        raw_line, pending = pending.split(b"\n", 1)
                        if len(raw_line) > GREP_MAX_JSON_RECORD_BYTES:
                            _terminate_process(process)
                            return _GrepPathResult(matches, stopped_by="max_bytes")
                        match = _grep_match_from_rg_event(
                            raw_line,
                            logical_path,
                            content_hash,
                        )
                        if match is None:
                            continue
                        matches.append(match)
                        if len(matches) >= match_limit:
                            _terminate_process(process)
                            return _GrepPathResult(matches)
                    if len(pending) > GREP_MAX_JSON_RECORD_BYTES:
                        _terminate_process(process)
                        return _GrepPathResult(matches, stopped_by="max_bytes")
                if pending:
                    if len(pending) > GREP_MAX_JSON_RECORD_BYTES:
                        return _GrepPathResult(matches, stopped_by="max_bytes")
                    match = _grep_match_from_rg_event(
                        pending,
                        logical_path,
                        content_hash,
                    )
                    if match is not None:
                        matches.append(match)
        except Exception:
            _terminate_process(process)
            raise
        finally:
            process.stdout.close()
        try:
            returncode = process.wait(timeout=max(deadline - time.monotonic(), 0.0))
        except subprocess.TimeoutExpired as exc:
            _terminate_process(process)
            raise TimeoutError from exc
        if returncode == 1:
            return _GrepPathResult(matches)
        if returncode == 2:
            raise RuntimeError("ctxfs grep failed")
        if returncode != 0:
            raise RuntimeError("ctxfs grep failed")
        return _GrepPathResult(matches)

    def _put(
        self,
        user_id: str,
        writer_id: str,
        operation: PutOperation,
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        path = _normalize_path(operation.path)
        self._require_authorized_path(user_id, writer_id, path)
        text = _normalize_text(operation.text)
        data = text.encode("utf-8")
        content_hash = _sha256(data)
        now = time.time()
        existing = self._entry_by_source_key(user_id, writer_id, operation.source_key)
        if existing is not None and existing.content_hash == content_hash:
            object_id = existing.object_id
            if existing.path != path:
                self._rename_tree_entry(
                    user_id,
                    existing.path,
                    path,
                    undo_stack,
                    cleanup_stack,
                )
        else:
            object_id = str(uuid.uuid4())
            object_path = self._object_path(user_id, object_id)
            object_path.parent.mkdir(parents=True, exist_ok=True)
            object_path.write_bytes(data)
            undo_stack.append(
                lambda object_path=object_path: self._remove_object_entry(
                    user_id,
                    object_path,
                )
            )
            self._replace_tree_entry(
                user_id,
                path,
                object_path,
                undo_stack,
                cleanup_stack,
            )
            if existing is not None and existing.path != path:
                self._remove_tree_entry(
                    user_id,
                    existing.path,
                    undo_stack,
                    cleanup_stack,
                )
            if existing is not None:
                old_object_path = self._object_path(user_id, existing.object_id)
                cleanup_stack.append(
                    lambda old_object_path=old_object_path: self._remove_object_entry(
                        user_id,
                        old_object_path,
                    )
                )
        created_at = existing.created_at if existing is not None else now
        self.connection.execute(
            """
            INSERT INTO entries(
                user_id, writer_id, path, source_key, source_version, doc_class,
                file_format, content_hash, size_bytes, line_count, object_id,
                provenance, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, writer_id, source_key) DO UPDATE SET
                path = excluded.path,
                source_version = excluded.source_version,
                doc_class = excluded.doc_class,
                file_format = excluded.file_format,
                content_hash = excluded.content_hash,
                size_bytes = excluded.size_bytes,
                line_count = excluded.line_count,
                object_id = excluded.object_id,
                provenance = excluded.provenance,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                writer_id,
                path,
                operation.source_key,
                operation.source_version,
                operation.doc_class.value,
                operation.file_format,
                content_hash,
                len(data),
                _line_count(text),
                object_id,
                operation.provenance.model_dump_json(),
                created_at,
                now,
            ),
        )

    def _move(
        self,
        user_id: str,
        writer_id: str,
        operation: MoveOperation,
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        self._move_batch(user_id, writer_id, [operation], undo_stack, cleanup_stack)

    def _move_batch(
        self,
        user_id: str,
        writer_id: str,
        operations: list[MoveOperation],
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        if not operations:
            return
        entries_by_source = {
            operation.source_key: self._validated_move_entry(
                user_id,
                writer_id,
                operation,
            )
            for operation in operations
        }
        current_locations = {
            entry.path: self._tree_path(user_id, entry.path)
            for entry in entries_by_source.values()
        }
        destinations = {
            entries_by_source[operation.source_key].path: _normalize_path(
                operation.path
            )
            for operation in operations
        }
        source_key_by_path = {
            entry.path: source_key for source_key, entry in entries_by_source.items()
        }
        temp_locations = self._break_move_cycles(
            user_id,
            destinations,
            current_locations,
            undo_stack,
            cleanup_stack,
        )
        for old_path, (_temp_path, temp_logical_path) in temp_locations.items():
            self.connection.execute(
                """
                UPDATE entries
                SET path = ?, updated_at = ?
                WHERE user_id = ? AND writer_id = ? AND source_key = ?
                """,
                (
                    temp_logical_path,
                    time.time(),
                    user_id,
                    writer_id,
                    source_key_by_path[old_path],
                ),
            )
        for operation in operations:
            entry = entries_by_source[operation.source_key]
            path = _normalize_path(operation.path)
            if entry.path != path:
                source = temp_locations.get(entry.path, (None, ""))[0]
                source = source or self._tree_path(
                    user_id,
                    entry.path,
                )
                target = self._tree_path(user_id, path)
                if source.exists():
                    self._rename_tree_file(
                        user_id,
                        source,
                        target,
                        entry.path,
                        undo_stack,
                        cleanup_stack,
                    )
                current_locations[entry.path] = target
            self.connection.execute(
                """
                UPDATE entries
                SET path = ?, source_version = ?, updated_at = ?
                WHERE user_id = ? AND writer_id = ? AND source_key = ?
                """,
                (
                    path,
                    operation.source_version,
                    time.time(),
                    user_id,
                    writer_id,
                    operation.source_key,
                ),
            )

    def _validated_move_entry(
        self,
        user_id: str,
        writer_id: str,
        operation: MoveOperation,
    ) -> Entry:
        path = _normalize_path(operation.path)
        self._require_authorized_path(user_id, writer_id, path)
        existing = self._entry_by_source_key(user_id, writer_id, operation.source_key)
        if existing is None:
            raise KeyError(f"unknown source_key: {operation.source_key}")
        self._require_authorized_path(user_id, writer_id, existing.path)
        if existing.content_hash != operation.expected_content_hash:
            raise ContentHashMismatchError(
                f"source {operation.source_key} has {existing.content_hash}, "
                f"expected {operation.expected_content_hash}"
            )
        return existing

    def _break_move_cycles(
        self,
        user_id: str,
        destinations: dict[str, str],
        current_locations: dict[str, Path],
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> dict[str, tuple[Path, str]]:
        temp_locations: dict[str, tuple[Path, str]] = {}
        for cycle in _move_cycles(destinations):
            source_path = destinations[cycle[0]]
            source = current_locations[source_path]
            if not source.exists():
                continue
            temp = self._undo_path("cycle")
            temp_logical_path = f"_ctxfs_cycle/{uuid.uuid4()}"
            self._rename_tree_file(
                user_id,
                source,
                temp,
                source_path,
                undo_stack,
                cleanup_stack,
            )
            temp_locations[source_path] = (temp, temp_logical_path)
            current_locations[source_path] = temp
        return temp_locations

    def _delete(
        self,
        user_id: str,
        writer_id: str,
        operation: DeleteOperation,
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        existing = self._entry_by_source_key(user_id, writer_id, operation.source_key)
        if existing is None:
            return
        self._require_authorized_path(user_id, writer_id, existing.path)
        self._remove_tree_entry(user_id, existing.path, undo_stack, cleanup_stack)
        self.connection.execute(
            """
            DELETE FROM entries
            WHERE user_id = ? AND writer_id = ? AND source_key = ?
            """,
            (user_id, writer_id, operation.source_key),
        )

    def _decision(
        self,
        user_id: str,
        writer_id: str,
        operation: DecisionOperation,
    ) -> None:
        path = _normalize_path(operation.path) if operation.path else None
        if path is not None:
            self._require_authorized_path(user_id, writer_id, path)
        now = time.time()
        existing = self.connection.execute(
            """
            SELECT created_at FROM decisions
            WHERE user_id = ? AND writer_id = ? AND source_key = ?
            """,
            (user_id, writer_id, operation.source_key),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO decisions(
                user_id, writer_id, source_key, source_version, path, state,
                failure_code, failure_message, retry_after_seconds, created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, writer_id, source_key) DO UPDATE SET
                source_version = excluded.source_version,
                path = excluded.path,
                state = excluded.state,
                failure_code = excluded.failure_code,
                failure_message = excluded.failure_message,
                retry_after_seconds = excluded.retry_after_seconds,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                writer_id,
                operation.source_key,
                operation.source_version,
                path,
                operation.state.value,
                operation.failure_code,
                operation.failure_message,
                operation.retry_after_seconds,
                existing["created_at"] if existing is not None else now,
                now,
            ),
        )

    def _require_entry(self, user_id: str, path: str) -> Entry:
        entry = self.stat(user_id, path)
        if entry is None:
            raise FileNotFoundError(_normalize_path(path))
        return entry

    def _entry_by_source_key(
        self,
        user_id: str,
        writer_id: str,
        source_key: str,
    ) -> Entry | None:
        row = self.connection.execute(
            """
            SELECT * FROM entries
            WHERE user_id = ? AND writer_id = ? AND source_key = ?
            """,
            (user_id, writer_id, source_key),
        ).fetchone()
        return None if row is None else _entry_from_row(row)

    def _preflight_submission(
        self,
        user_id: str,
        writer_id: str,
        operations: list[CtxfsOperation],
    ) -> None:
        paths_by_source, sources_by_path = self._source_path_maps(user_id)
        moving_identities = {
            (writer_id, operation.source_key)
            for operation in operations
            if operation.kind == OperationKind.MOVE
        }
        identity = (writer_id, "")
        for operation in operations:
            if operation.kind == OperationKind.PUT:
                path = _normalize_path(operation.path)
                self._require_authorized_path(user_id, writer_id, path)
                identity = (writer_id, operation.source_key)
                self._require_unoccupied_path(path, identity, sources_by_path)
                old_path = paths_by_source.get(identity)
                if old_path is not None and old_path != path:
                    sources_by_path.pop(old_path, None)
                paths_by_source[identity] = path
                sources_by_path[path] = identity
            elif operation.kind == OperationKind.MOVE:
                path = _normalize_path(operation.path)
                self._require_authorized_path(user_id, writer_id, path)
                identity = (writer_id, operation.source_key)
                old_path = paths_by_source.get(identity)
                if old_path is None:
                    raise KeyError(f"unknown source_key: {operation.source_key}")
                self._require_authorized_path(user_id, writer_id, old_path)
                occupant = sources_by_path.get(path)
                if (
                    occupant is not None
                    and occupant != identity
                    and occupant not in moving_identities
                ):
                    self._require_unoccupied_path(path, identity, sources_by_path)
                if old_path != path:
                    sources_by_path.pop(old_path, None)
                    paths_by_source[identity] = path
                    sources_by_path[path] = identity
            elif operation.kind == OperationKind.DELETE:
                identity = (writer_id, operation.source_key)
                old_path = paths_by_source.get(identity)
                if old_path is None:
                    continue
                self._require_authorized_path(user_id, writer_id, old_path)
                paths_by_source.pop(identity, None)
                sources_by_path.pop(old_path, None)
            elif operation.kind == OperationKind.DECISION:
                if operation.path is not None:
                    self._require_authorized_path(
                        user_id,
                        writer_id,
                        _normalize_path(operation.path),
                    )
            else:
                raise ValueError(f"unsupported operation: {operation.kind}")

    def _reject_duplicate_move_sources(
        self,
        writer_id: str,
        operations: list[CtxfsOperation],
    ) -> None:
        seen: set[SourceIdentity] = set()
        for operation in operations:
            if operation.kind != OperationKind.MOVE:
                continue
            identity = (writer_id, operation.source_key)
            if identity in seen:
                raise ValueError("duplicate move source_key in submission")
            seen.add(identity)

    def _ordered_operations(
        self,
        user_id: str,
        writer_id: str,
        operations: list[CtxfsOperation],
    ) -> list[CtxfsOperation]:
        deletes = [
            operation
            for operation in operations
            if operation.kind == OperationKind.DELETE
        ]
        moves = [
            operation
            for operation in operations
            if operation.kind == OperationKind.MOVE
        ]
        puts = [
            operation for operation in operations if operation.kind == OperationKind.PUT
        ]
        decisions = [
            operation
            for operation in operations
            if operation.kind == OperationKind.DECISION
        ]
        if len(moves) < 2:
            return [*deletes, *moves, *puts, *decisions]
        paths_by_source, _ = self._source_path_maps(user_id)
        move_by_source_path: dict[str, MoveOperation] = {}
        for move in moves:
            source_path = paths_by_source.get((writer_id, move.source_key))
            if source_path is not None:
                move_by_source_path[source_path] = move

        ordered_moves: list[MoveOperation] = []
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(move: MoveOperation) -> None:
            if move.source_key in permanent:
                return
            if move.source_key in temporary:
                return
            temporary.add(move.source_key)
            destination = _normalize_path(move.path)
            dependency = move_by_source_path.get(destination)
            if dependency is not None and dependency.source_key != move.source_key:
                visit(dependency)
            temporary.remove(move.source_key)
            permanent.add(move.source_key)
            ordered_moves.append(move)

        for move in moves:
            visit(move)

        return [*deletes, *ordered_moves, *puts, *decisions]

    def _source_path_maps(
        self,
        user_id: str,
    ) -> tuple[dict[SourceIdentity, str], dict[str, SourceIdentity]]:
        rows = self.connection.execute(
            "SELECT writer_id, source_key, path FROM entries WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        paths_by_source: dict[SourceIdentity, str] = {}
        sources_by_path: dict[str, SourceIdentity] = {}
        for row in rows:
            identity = (row["writer_id"], row["source_key"])
            paths_by_source[identity] = row["path"]
            sources_by_path[row["path"]] = identity
        return paths_by_source, sources_by_path

    def _require_unoccupied_path(
        self,
        path: str,
        identity: SourceIdentity,
        sources_by_path: dict[str, SourceIdentity],
    ) -> None:
        occupant = sources_by_path.get(path)
        if occupant is None or occupant == identity:
            return
        raise FileExistsError(
            f"path {path} is already occupied by writer {occupant[0]} "
            f"source_key {occupant[1]}"
        )

    def _entries_under_prefix(self, user_id: str, prefix: str) -> list[Entry]:
        rows = self.connection.execute(
            "SELECT * FROM entries WHERE user_id = ? ORDER BY path",
            (user_id,),
        ).fetchall()
        return [
            _entry_from_row(row)
            for row in rows
            if not prefix or _path_is_within_prefix(row["path"], prefix)
        ]

    def _decision_state_counts(
        self,
        user_id: str,
        writer_id: str,
        *,
        prefix: str | None,
    ) -> dict[DecisionState, int]:
        if prefix:
            rows = self.connection.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM decisions
                WHERE user_id = ? AND writer_id = ? AND path IS NOT NULL
                  AND (path = ? OR path LIKE ?)
                GROUP BY state
                """,
                (user_id, writer_id, prefix, f"{prefix}/%"),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM decisions
                WHERE user_id = ? AND writer_id = ?
                GROUP BY state
                """,
                (user_id, writer_id),
            ).fetchall()
        return {DecisionState(row["state"]): row["count"] for row in rows}

    def _require_authorized_path(
        self,
        user_id: str,
        writer_id: str,
        path: str,
    ) -> None:
        rows = self.connection.execute(
            """
            SELECT prefix FROM writer_prefixes
            WHERE user_id = ? AND writer_id = ?
            """,
            (user_id, writer_id),
        ).fetchall()
        if any(_path_is_within_prefix(path, row["prefix"]) for row in rows):
            return
        raise PrefixAuthorizationError(
            f"writer {writer_id} is not authorized for path {path}"
        )

    def _user_root(self, user_id: str) -> Path:
        return self.tree_root / _disk_name(user_id)

    def _tree_path(self, user_id: str, path: str) -> Path:
        return self._user_root(user_id).joinpath(
            *(_tree_component_name(part) for part in PurePosixPath(path).parts)
        )

    def _object_path(self, user_id: str, object_id: str) -> Path:
        return self.objects_root / _disk_name(user_id) / object_id

    def _disk_files_under_prefix(
        self,
        user_id: str,
        prefix: str,
    ) -> list[tuple[str, Path]]:
        root = self._user_root(user_id)
        if not root.exists():
            return []
        start = self._tree_path(user_id, prefix) if prefix else root
        if not start.exists():
            return []
        candidates = [start] if start.is_file() else sorted(start.rglob("*"))
        files: list[tuple[str, Path]] = []
        for candidate in candidates:
            if not candidate.is_file():
                continue
            logical_path = _logical_tree_path(root, candidate)
            if logical_path is None:
                continue
            if prefix and not _path_is_within_prefix(logical_path, prefix):
                continue
            files.append((logical_path, candidate))
        return files

    def _replace_tree_entry(
        self,
        user_id: str,
        path: str,
        object_path: Path,
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        target = self._tree_path(user_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._undo_path("replace")
        backup = self._backup_tree_entry(target)
        try:
            os.link(object_path, tmp)
            os.replace(tmp, target)
        except Exception:
            _unlink_if_exists(tmp)
            _unlink_if_exists(backup)
            raise
        undo_stack.append(
            lambda target=target, backup=backup: self._undo_replace_tree_entry(
                user_id,
                target,
                backup,
            )
        )
        cleanup_stack.append(lambda backup=backup: _unlink_if_exists(backup))

    def _rename_tree_entry(
        self,
        user_id: str,
        old_path: str,
        new_path: str,
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        source = self._tree_path(user_id, old_path)
        target = self._tree_path(user_id, new_path)
        self._rename_tree_file(
            user_id,
            source,
            target,
            old_path,
            undo_stack,
            cleanup_stack,
        )

    def _rename_tree_file(
        self,
        user_id: str,
        source: Path,
        target: Path,
        missing_path: str,
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        if not source.exists():
            raise FileNotFoundError(missing_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = self._backup_tree_entry(target)
        try:
            os.replace(source, target)
        except Exception:
            _unlink_if_exists(backup)
            raise
        _remove_empty_parents(source.parent, self._user_root(user_id))
        undo_stack.append(
            lambda source=source, target=target, backup=backup: (
                self._undo_rename_tree_entry(user_id, source, target, backup)
            )
        )
        cleanup_stack.append(lambda backup=backup: _unlink_if_exists(backup))

    def _remove_tree_entry(
        self,
        user_id: str,
        path: str,
        undo_stack: list[FilesystemCallback],
        cleanup_stack: list[FilesystemCallback],
    ) -> None:
        target = self._tree_path(user_id, path)
        backup = self._backup_tree_entry(target)
        try:
            target.unlink()
        except FileNotFoundError:
            _unlink_if_exists(backup)
            return
        _remove_empty_parents(target.parent, self._user_root(user_id))
        undo_stack.append(
            lambda target=target, backup=backup: self._undo_remove_tree_entry(
                target,
                backup,
            )
        )
        cleanup_stack.append(lambda backup=backup: _unlink_if_exists(backup))

    def _remove_object_entry(self, user_id: str, object_path: Path) -> None:
        _unlink_if_exists(object_path)
        _remove_empty_parents(
            object_path.parent, self.objects_root / _disk_name(user_id)
        )

    def _backup_tree_entry(self, target: Path) -> Path | None:
        if not target.exists():
            return None
        backup = self._undo_path("backup")
        os.link(target, backup)
        return backup

    def _undo_path(self, prefix: str) -> Path:
        self.undo_root.mkdir(parents=True, exist_ok=True)
        return self.undo_root / f"{prefix}-{uuid.uuid4()}"

    def _undo_replace_tree_entry(
        self,
        user_id: str,
        target: Path,
        backup: Path | None,
    ) -> None:
        if backup is None:
            _unlink_if_exists(target)
            _remove_empty_parents(target.parent, self._user_root(user_id))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backup, target)

    def _undo_rename_tree_entry(
        self,
        user_id: str,
        source: Path,
        target: Path,
        backup: Path | None,
    ) -> None:
        source.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            os.replace(target, source)
        if backup is None:
            _remove_empty_parents(target.parent, self._user_root(user_id))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backup, target)

    def _undo_remove_tree_entry(self, target: Path, backup: Path | None) -> None:
        if backup is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backup, target)

    def _migrate_entries_table(self) -> None:
        if not _table_exists(self.connection, "entries"):
            self._create_entries_table()
            return
        columns = _table_columns(self.connection, "entries")
        if "writer_id" in columns and _has_scoped_source_key_index(self.connection):
            return

        legacy_table = f"entries_legacy_{uuid.uuid4().hex}"
        self.connection.execute(f"ALTER TABLE entries RENAME TO {legacy_table}")
        self._create_entries_table()
        legacy_columns = _table_columns(self.connection, legacy_table)
        writer_expr = (
            "writer_id"
            if "writer_id" in legacy_columns
            else """
                COALESCE(
                    (
                        SELECT writer_prefixes.writer_id
                        FROM writer_prefixes
                        WHERE writer_prefixes.user_id = legacy.user_id
                          AND (
                              legacy.path = writer_prefixes.prefix
                              OR legacy.path LIKE writer_prefixes.prefix || '/%'
                          )
                        ORDER BY length(writer_prefixes.prefix) DESC
                        LIMIT 1
                    ),
                    '__legacy__'
                )
            """
        )
        self.connection.execute(
            f"""
            INSERT INTO entries(
                user_id, writer_id, path, source_key, source_version, doc_class,
                file_format, content_hash, size_bytes, line_count, object_id,
                provenance, created_at, updated_at
            )
            SELECT
                user_id,
                {writer_expr},
                path,
                source_key,
                source_version,
                doc_class,
                file_format,
                content_hash,
                size_bytes,
                line_count,
                object_id,
                provenance,
                created_at,
                updated_at
            FROM {legacy_table} AS legacy
            """
        )
        self.connection.execute(f"DROP TABLE {legacy_table}")

    def _create_entries_table(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE entries (
                user_id TEXT NOT NULL,
                writer_id TEXT NOT NULL,
                path TEXT NOT NULL,
                source_key TEXT NOT NULL,
                source_version TEXT NOT NULL,
                doc_class TEXT NOT NULL,
                file_format TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                line_count INTEGER NOT NULL,
                object_id TEXT NOT NULL,
                provenance TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(user_id, path),
                UNIQUE(user_id, writer_id, source_key)
            )
            """
        )


def _entry_from_row(row: sqlite3.Row) -> Entry:
    return Entry(
        path=row["path"],
        source_key=row["source_key"],
        source_version=row["source_version"],
        doc_class=row["doc_class"],
        file_format=row["file_format"],
        content_hash=row["content_hash"],
        size_bytes=row["size_bytes"],
        line_count=row["line_count"],
        object_id=row["object_id"],
        provenance=Provenance.model_validate_json(row["provenance"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _normalize_path(path: str) -> str:
    if path == "" or path.startswith("/") or _has_control_character(path):
        raise ValueError(f"invalid ctxfs path: {path}")
    if path.startswith(tuple(f"{letter}:" for letter in _ASCII_LETTERS)):
        raise ValueError(f"invalid ctxfs path: {path}")
    raw_parts = path.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise ValueError(f"invalid ctxfs path: {path}")
    raw = PurePosixPath(path)
    if raw.is_absolute():
        raise ValueError("ctxfs paths must be relative")
    parts = raw.parts
    if not parts or any(
        part in ("", ".", "..") or _has_control_character(part) for part in parts
    ):
        raise ValueError(f"invalid ctxfs path: {path}")
    return "/".join(parts)


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_glob_pattern(pattern: str) -> tuple[str, ...]:
    if pattern == "" or pattern.endswith("/"):
        raise ValueError(f"invalid ctxfs glob pattern: {pattern}")
    if _has_unclosed_character_class(pattern):
        raise ValueError(f"invalid ctxfs glob pattern: {pattern}")
    parts = tuple(pattern.split("/"))
    if any(part == "" for part in parts):
        raise ValueError(f"invalid ctxfs glob pattern: {pattern}")
    if any("**" in part and part != "**" for part in parts):
        raise ValueError(f"invalid ctxfs glob pattern: {pattern}")
    return parts


def _has_unclosed_character_class(pattern: str) -> bool:
    in_class = False
    for character in pattern:
        if character == "[":
            in_class = True
        elif character == "]" and in_class:
            in_class = False
    return in_class


def _glob_matches(pattern_parts: tuple[str, ...], path_parts: tuple[str, ...]) -> bool:
    if not pattern_parts:
        return not path_parts
    head, *tail = pattern_parts
    tail_parts = tuple(tail)
    if head == "**":
        return any(
            _glob_matches(tail_parts, path_parts[index:])
            for index in range(len(path_parts) + 1)
        )
    if not path_parts:
        return False
    return fnmatch.fnmatchcase(path_parts[0], head) and _glob_matches(
        tail_parts,
        path_parts[1:],
    )


def _move_cycles(destinations: dict[str, str]) -> list[list[str]]:
    cycles: list[list[str]] = []
    seen: set[str] = set()
    for start in destinations:
        if start in seen:
            continue
        path = start
        stack: list[str] = []
        stack_index: dict[str, int] = {}
        while path in destinations:
            if path in stack_index:
                cycles.append(stack[stack_index[path] :])
                break
            if path in seen:
                break
            stack_index[path] = len(stack)
            stack.append(path)
            path = destinations[path]
        seen.update(stack)
    return cycles


def _path_is_within_prefix(path: str, prefix: str) -> bool:
    normalized_path = _normalize_path(path)
    normalized_prefix = _normalize_path(prefix)
    return normalized_path == normalized_prefix or normalized_path.startswith(
        f"{normalized_prefix}/"
    )


def _prefixes_overlap(left: str, right: str) -> bool:
    return _path_is_within_prefix(left, right) or _path_is_within_prefix(right, left)


def _join_relative(prefix: str, name: str) -> str:
    return name if not prefix else f"{prefix}/{name}"


def _relative_parts(prefix: str, path: str) -> tuple[str, ...]:
    if not prefix:
        return PurePosixPath(path).parts
    if path == prefix:
        return ()
    if not path.startswith(f"{prefix}/"):
        return ()
    return PurePosixPath(path[len(prefix) + 1 :]).parts


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _normalize_text(text: str) -> str:
    if "\x00" in text:
        raise ValueError("ctxfs text must not contain NUL")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _truncate_utf8(data: bytes, max_bytes: int) -> bytes:
    limited = data[:max_bytes]
    while limited:
        try:
            limited.decode("utf-8")
        except UnicodeDecodeError:
            limited = limited[:-1]
            continue
        return limited
    return b""


def _line_count(text: str) -> int:
    if text == "":
        return 0
    return len(text.splitlines())


def _disk_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tree_component_name(value: str) -> str:
    return f"p-{value.encode('utf-8').hex()}"


def _logical_tree_path(root: Path, path: Path) -> str | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    parts: list[str] = []
    for part in relative.parts:
        logical = _logical_component_name(part)
        if logical is None:
            return None
        parts.append(logical)
    return "/".join(parts)


def _logical_component_name(value: str) -> str | None:
    if not value.startswith("p-"):
        return None
    try:
        return bytes.fromhex(value.removeprefix("p-")).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def _disk_directory_entry(path: str, tree_path: Path) -> DirectoryEntry:
    data = tree_path.read_bytes()
    text = data.decode("utf-8", "replace")
    return DirectoryEntry(
        path=path,
        kind="file",
        size_bytes=len(data),
        line_count=_line_count(text),
    )


def _rg_command(pattern: str, path: Path) -> list[str]:
    return [
        "rg",
        "--json",
        "--no-follow",
        "--sort",
        "path",
        "--hidden",
        "--no-ignore",
        "--no-config",
        "--binary",
        "-e",
        pattern,
        "--",
        str(path),
    ]


def _has_rg() -> bool:
    return shutil.which("rg", path=os.environ.get("PATH", "")) is not None


def _python_grep_tree_path(
    pattern: str,
    tree_path: Path,
    logical_path: str,
    content_hash: str,
    match_limit: int,
) -> _GrepPathResult:
    if _may_backtrack_pathologically(pattern):
        return _GrepPathResult([], stopped_by="timeout")

    compiled = re.compile(pattern)
    matches: list[GrepMatch] = []
    for line_number, raw_line in enumerate(tree_path.read_bytes().splitlines(), 1):
        line = raw_line.decode("utf-8", "replace")
        match = compiled.search(line)
        if match is None:
            continue
        matches.append(
            GrepMatch(
                path=logical_path,
                line_number=line_number,
                line=line,
                match_start=match.start(),
                match_end=match.end(),
                content_hash=content_hash,
            )
        )
        if len(matches) >= match_limit:
            return _GrepPathResult(matches)
    return _GrepPathResult(matches)


def _may_backtrack_pathologically(pattern: str) -> bool:
    return re.search(r"\([^)]*[+*][^)]*\)[+*{]", pattern) is not None


def _rg_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "")}
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _grep_match_from_rg_event(
    raw_line: bytes,
    logical_path: str,
    content_hash: str,
) -> GrepMatch | None:
    event = json.loads(raw_line)
    if event.get("type") != "match":
        return None
    data = event["data"]
    lines = data["lines"]
    line = _rg_line_text(lines).rstrip("\r\n")
    submatch = data["submatches"][0]
    match_start = _rg_byte_offset_to_char_offset(lines, submatch["start"])
    match_end = _rg_byte_offset_to_char_offset(lines, submatch["end"])
    return GrepMatch(
        path=logical_path,
        line_number=data["line_number"],
        line=line,
        match_start=match_start,
        match_end=match_end,
        content_hash=content_hash,
    )


def _rg_line_text(lines: dict[str, str]) -> str:
    if "text" in lines:
        return lines["text"]
    return base64.b64decode(lines["bytes"]).decode("utf-8", "replace")


def _rg_byte_offset_to_char_offset(lines: dict[str, str], byte_offset: int) -> int:
    if "bytes" in lines:
        raw = base64.b64decode(lines["bytes"])
    else:
        raw = lines["text"].encode("utf-8")
    return len(raw[:byte_offset].decode("utf-8", "replace"))


def _rollback_filesystem(
    undo_stack: list[FilesystemCallback],
) -> RuntimeError | None:
    errors: list[Exception] = []
    for undo in reversed(undo_stack):
        try:
            undo()
        except Exception as exc:
            errors.append(exc)
    if not errors:
        return None
    rollback_error = RuntimeError("failed to roll back ctxfs filesystem mutation")
    rollback_error.__cause__ = errors[0]
    return rollback_error


def _cleanup_filesystem(cleanup_stack: list[FilesystemCallback]) -> None:
    for cleanup in cleanup_stack:
        try:
            cleanup()
        except Exception:
            logger.warning("failed to clean up ctxfs filesystem journal", exc_info=True)


def _unlink_if_exists(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _has_scoped_source_key_index(connection: sqlite3.Connection) -> bool:
    for index in connection.execute("PRAGMA index_list(entries)").fetchall():
        if not index["unique"]:
            continue
        columns = [
            row["name"]
            for row in connection.execute(f"PRAGMA index_info({index['name']})")
        ]
        if columns == ["user_id", "writer_id", "source_key"]:
            return True
    return False


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    stop_at = stop_at.resolve()
    current = path
    while True:
        try:
            current.resolve().relative_to(stop_at)
        except ValueError:
            return
        if current.resolve() == stop_at:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
