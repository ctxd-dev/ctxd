from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from ctxfs.models import (
    Bounded,
    CtxfsOperation,
    DeletionResult,
    DirectoryEntry,
    Entry,
    GrepMatch,
    OperationKind,
    ReadLinesResult,
    ReadResult,
    Submission,
    SubmissionState,
)
from ctxfs.store import (
    CtxfsStore,
    _disk_name,
    _normalize_path,
    _path_is_within_prefix,
    _remove_empty_parents,
)
from pydantic import TypeAdapter

_OPERATIONS_ADAPTER = TypeAdapter(list[CtxfsOperation])


class CtxfsService:
    """Target service boundary over the in-process store.

    The store remains the synchronous filesystem/catalog primitive. The service
    layer models the deployable boundary: writes are accepted into a durable
    queue, an applier drains that queue, and reads use a read-only surface.
    """

    def __init__(self, root: Path):
        self.root = root
        self._store = CtxfsStore(root)
        self._apply_migrations()
        self.read = CtxfsReadService(self._store)
        self.write = CtxfsWriteService(self._store)
        self.applier = CtxfsApplier(self._store)
        self.maintainer = CtxfsMaintainer(self._store)
        self.management = CtxfsManagementService(self._store)

    def close(self) -> None:
        self._store.close()

    def preflight(self) -> None:
        self.maintainer.preflight()

    def _apply_migrations(self) -> None:
        with self._store.connection:
            self._store.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS service_submissions (
                    submission_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    writer_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    operation_count INTEGER NOT NULL,
                    operations_json TEXT NOT NULL,
                    applied_submission_id TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._store.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deletion_records (
                    deletion_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    writer_id TEXT,
                    prefix TEXT,
                    receipt TEXT,
                    entry_count INTEGER NOT NULL,
                    decision_count INTEGER NOT NULL,
                    queued_submission_count INTEGER NOT NULL,
                    writer_registration_count INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )


class CtxfsReadService:
    def __init__(self, store: CtxfsStore):
        self._store = store

    def stat(self, user_id: str, path: str) -> Entry | None:
        return self._store.stat(user_id, path)

    def ls(
        self,
        user_id: str,
        path: str,
        *,
        limit: int = 1000,
    ) -> Bounded[DirectoryEntry]:
        return self._store.ls(user_id, path, limit=limit)

    def tree(
        self,
        user_id: str,
        prefix: str = "",
        *,
        depth: int | None = None,
        limit: int = 1000,
    ) -> Bounded[DirectoryEntry]:
        return self._store.tree(user_id, prefix, depth=depth, limit=limit)

    def glob(
        self,
        user_id: str,
        pattern: str,
        *,
        prefix: str = "",
        limit: int = 1000,
    ) -> Bounded[DirectoryEntry]:
        return self._store.glob(user_id, pattern, prefix=prefix, limit=limit)

    def read(
        self,
        user_id: str,
        path: str,
        *,
        max_bytes: int = 1_000_000,
    ) -> ReadResult:
        return self._store.read(user_id, path, max_bytes=max_bytes)

    def read_lines(
        self,
        user_id: str,
        path: str,
        start_line: int,
        end_line: int,
    ) -> ReadLinesResult:
        return self._store.read_lines(user_id, path, start_line, end_line)

    def grep(
        self,
        user_id: str,
        pattern: str,
        *,
        prefix: str = "",
        limit: int = 1000,
    ) -> Bounded[GrepMatch]:
        return self._store.grep(user_id, pattern, prefix=prefix, limit=limit)


class CtxfsWriteService:
    def __init__(self, store: CtxfsStore):
        self._store = store

    def register_prefix(self, user_id: str, writer_id: str, prefix: str) -> None:
        self._store.register_prefix(user_id, writer_id, prefix)

    def submit(
        self,
        user_id: str,
        writer_id: str,
        operations: list[CtxfsOperation],
    ) -> Submission:
        submission_id = str(uuid.uuid4())
        now = time.time()
        payload = json.dumps(
            [operation.model_dump(mode="json") for operation in operations],
            separators=(",", ":"),
        )
        with self._store.connection:
            self._store.connection.execute(
                """
                INSERT INTO service_submissions(
                    submission_id, user_id, writer_id, state, operation_count,
                    operations_json, applied_submission_id, error, created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    submission_id,
                    user_id,
                    writer_id,
                    SubmissionState.QUEUED.value,
                    len(operations),
                    payload,
                    now,
                    now,
                ),
            )
        return Submission(
            submission_id=submission_id,
            state=SubmissionState.QUEUED,
            operation_count=len(operations),
        )


class CtxfsApplier:
    def __init__(self, store: CtxfsStore):
        self._store = store

    def apply_next(self) -> Submission | None:
        row = self._store.connection.execute(
            """
            SELECT * FROM service_submissions
            WHERE state = ?
            ORDER BY created_at, submission_id
            LIMIT 1
            """,
            (SubmissionState.QUEUED.value,),
        ).fetchone()
        if row is None:
            return None
        operations = _OPERATIONS_ADAPTER.validate_json(row["operations_json"])
        try:
            applied = self._store.submit(row["user_id"], row["writer_id"], operations)
        except Exception as exc:
            return self._mark_failed(row["submission_id"], row["operation_count"], exc)
        return self._mark_applied(
            row["submission_id"],
            row["operation_count"],
            applied.submission_id,
        )

    def drain(self, *, limit: int | None = None) -> list[Submission]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        applied: list[Submission] = []
        while limit is None or len(applied) < limit:
            submission = self.apply_next()
            if submission is None:
                break
            applied.append(submission)
        return applied

    def _mark_applied(
        self,
        submission_id: str,
        operation_count: int,
        applied_submission_id: str,
    ) -> Submission:
        with self._store.connection:
            self._store.connection.execute(
                """
                UPDATE service_submissions
                SET state = ?, applied_submission_id = ?, error = NULL,
                    updated_at = ?
                WHERE submission_id = ?
                """,
                (
                    SubmissionState.APPLIED.value,
                    applied_submission_id,
                    time.time(),
                    submission_id,
                ),
            )
        return Submission(
            submission_id=submission_id,
            state=SubmissionState.APPLIED,
            operation_count=operation_count,
        )

    def _mark_failed(
        self,
        submission_id: str,
        operation_count: int,
        exc: Exception,
    ) -> Submission:
        error = str(exc) or exc.__class__.__name__
        with self._store.connection:
            self._store.connection.execute(
                """
                UPDATE service_submissions
                SET state = ?, error = ?, updated_at = ?
                WHERE submission_id = ?
                """,
                (SubmissionState.FAILED.value, error, time.time(), submission_id),
            )
        return Submission(
            submission_id=submission_id,
            state=SubmissionState.FAILED,
            operation_count=operation_count,
            error=error,
        )


class CtxfsMaintainer:
    def __init__(self, store: CtxfsStore):
        self._store = store

    def preflight(self) -> None:
        for path in (
            self._store.root,
            self._store.tree_root,
            self._store.objects_root,
            self._store.undo_root,
        ):
            if not path.exists():
                raise FileNotFoundError(path)
            if not path.is_dir():
                raise NotADirectoryError(path)
        self._store.connection.execute("PRAGMA quick_check").fetchone()

    def queue_depth(self) -> int:
        row = self._store.connection.execute(
            """
            SELECT COUNT(*) AS count FROM service_submissions
            WHERE state = ?
            """,
            (SubmissionState.QUEUED.value,),
        ).fetchone()
        return int(row["count"])


class CtxfsManagementService:
    def __init__(self, store: CtxfsStore):
        self._store = store

    def delete_prefix(
        self,
        user_id: str,
        writer_id: str,
        prefix: str,
        *,
        receipt: str | None = None,
    ) -> DeletionResult:
        normalized_prefix = self._normalize_prefix(prefix)
        self._store._require_authorized_path(user_id, writer_id, normalized_prefix)
        rows = self._entry_rows_under_prefix(
            user_id,
            writer_id=writer_id,
            prefix=normalized_prefix,
        )
        source_keys = [row["source_key"] for row in rows]
        decision_count = self._delete_decisions_for_prefix(
            user_id,
            writer_id,
            normalized_prefix,
            source_keys,
        )
        queued_count = self._discard_queued_submissions_for_prefix(
            user_id,
            writer_id,
            normalized_prefix,
            source_keys,
        )
        self._delete_entry_rows_and_files(user_id, rows)
        result = DeletionResult(
            scope="prefix",
            user_id=user_id,
            writer_id=writer_id,
            prefix=normalized_prefix,
            receipt=receipt,
            entry_count=len(rows),
            decision_count=decision_count,
            queued_submission_count=queued_count,
            writer_registration_count=0,
        )
        self._record_deletion(result)
        return result

    def delete_integration(
        self,
        user_id: str,
        writer_id: str,
        *,
        receipt: str | None = None,
    ) -> DeletionResult:
        prefix_rows = self._store.connection.execute(
            """
            SELECT prefix FROM writer_prefixes
            WHERE user_id = ? AND writer_id = ?
            ORDER BY prefix
            """,
            (user_id, writer_id),
        ).fetchall()
        prefixes = [row["prefix"] for row in prefix_rows]
        rows = self._entry_rows_for_writer(user_id, writer_id)
        decision_count = self._delete_decisions_for_writer(user_id, writer_id)
        queued_count = self._discard_queued_submissions_for_writer(user_id, writer_id)
        self._delete_entry_rows_and_files(user_id, rows)
        with self._store.connection:
            cursor = self._store.connection.execute(
                """
                DELETE FROM writer_prefixes
                WHERE user_id = ? AND writer_id = ?
                """,
                (user_id, writer_id),
            )
            writer_registration_count = cursor.rowcount
        result = DeletionResult(
            scope="integration",
            user_id=user_id,
            writer_id=writer_id,
            prefix=",".join(prefixes) if prefixes else None,
            receipt=receipt,
            entry_count=len(rows),
            decision_count=decision_count,
            queued_submission_count=queued_count,
            writer_registration_count=writer_registration_count,
        )
        self._record_deletion(result)
        return result

    def delete_user(self, user_id: str, *, receipt: str) -> DeletionResult:
        if not receipt:
            raise ValueError("delete_user requires a receipt")
        rows = self._entry_rows_for_user(user_id)
        decision_count = self._delete_decisions_for_user(user_id)
        queued_count = self._delete_queued_submissions_for_user(user_id)
        with self._store.connection:
            cursor = self._store.connection.execute(
                "DELETE FROM writer_prefixes WHERE user_id = ?",
                (user_id,),
            )
            writer_registration_count = cursor.rowcount
            self._store.connection.execute(
                "DELETE FROM submissions WHERE user_id = ?",
                (user_id,),
            )
            self._store.connection.execute(
                "DELETE FROM service_submissions WHERE user_id = ?",
                (user_id,),
            )
            self._store.connection.execute(
                "DELETE FROM entries WHERE user_id = ?",
                (user_id,),
            )
        shutil.rmtree(self._store._user_root(user_id), ignore_errors=True)
        shutil.rmtree(
            self._store.objects_root / _disk_name(user_id),
            ignore_errors=True,
        )
        result = DeletionResult(
            scope="user",
            user_id=user_id,
            receipt=receipt,
            entry_count=len(rows),
            decision_count=decision_count,
            queued_submission_count=queued_count,
            writer_registration_count=writer_registration_count,
        )
        self._record_deletion(result)
        self._assert_user_removed(user_id)
        return result

    def _entry_rows_under_prefix(
        self,
        user_id: str,
        *,
        writer_id: str,
        prefix: str,
    ) -> list:
        rows = self._store.connection.execute(
            """
            SELECT * FROM entries
            WHERE user_id = ? AND writer_id = ?
            ORDER BY path
            """,
            (user_id, writer_id),
        ).fetchall()
        return [row for row in rows if _path_is_within_prefix(row["path"], prefix)]

    def _entry_rows_for_writer(self, user_id: str, writer_id: str) -> list:
        return self._store.connection.execute(
            """
            SELECT * FROM entries
            WHERE user_id = ? AND writer_id = ?
            ORDER BY path
            """,
            (user_id, writer_id),
        ).fetchall()

    def _entry_rows_for_user(self, user_id: str) -> list:
        return self._store.connection.execute(
            """
            SELECT * FROM entries
            WHERE user_id = ?
            ORDER BY path
            """,
            (user_id,),
        ).fetchall()

    def _delete_entry_rows_and_files(self, user_id: str, rows: list) -> None:
        for row in rows:
            tree_path = self._store._tree_path(user_id, row["path"])
            tree_path.unlink(missing_ok=True)
            _remove_empty_parents(tree_path.parent, self._store._user_root(user_id))
            object_path = self._store._object_path(user_id, row["object_id"])
            object_path.unlink(missing_ok=True)
            _remove_empty_parents(
                object_path.parent,
                self._store.objects_root / _disk_name(user_id),
            )
        with self._store.connection:
            for row in rows:
                self._store.connection.execute(
                    """
                    DELETE FROM entries
                    WHERE user_id = ? AND writer_id = ? AND source_key = ?
                    """,
                    (user_id, row["writer_id"], row["source_key"]),
                )

    def _delete_decisions_for_prefix(
        self,
        user_id: str,
        writer_id: str,
        prefix: str,
        source_keys: list[str],
    ) -> int:
        clauses = ["(path IS NOT NULL AND (path = ? OR path LIKE ?))"]
        params: list[str] = [prefix, f"{prefix}/%"]
        if source_keys:
            placeholders = ",".join("?" for _ in source_keys)
            clauses.append(f"source_key IN ({placeholders})")
            params.extend(source_keys)
        with self._store.connection:
            cursor = self._store.connection.execute(
                f"""
                DELETE FROM decisions
                WHERE user_id = ? AND writer_id = ? AND ({' OR '.join(clauses)})
                """,
                [user_id, writer_id, *params],
            )
        return cursor.rowcount

    def _delete_decisions_for_writer(self, user_id: str, writer_id: str) -> int:
        with self._store.connection:
            cursor = self._store.connection.execute(
                "DELETE FROM decisions WHERE user_id = ? AND writer_id = ?",
                (user_id, writer_id),
            )
        return cursor.rowcount

    def _delete_decisions_for_user(self, user_id: str) -> int:
        with self._store.connection:
            cursor = self._store.connection.execute(
                "DELETE FROM decisions WHERE user_id = ?",
                (user_id,),
            )
        return cursor.rowcount

    def _discard_queued_submissions_for_prefix(
        self,
        user_id: str,
        writer_id: str,
        prefix: str,
        source_keys: list[str],
    ) -> int:
        rows = self._queued_service_submissions(user_id, writer_id=writer_id)
        discarded = 0
        for row in rows:
            operations = _OPERATIONS_ADAPTER.validate_json(row["operations_json"])
            kept_operations = [
                operation
                for operation in operations
                if not self._operation_targets_prefix(operation, prefix, source_keys)
            ]
            if len(kept_operations) == len(operations):
                continue
            if kept_operations:
                self._replace_service_submission_operations(
                    row["submission_id"],
                    kept_operations,
                )
            else:
                self._discard_service_submission(row["submission_id"])
            discarded += 1
        return discarded

    def _replace_service_submission_operations(
        self,
        submission_id: str,
        operations: list[CtxfsOperation],
    ) -> None:
        payload = json.dumps(
            [operation.model_dump(mode="json") for operation in operations],
            separators=(",", ":"),
        )
        with self._store.connection:
            self._store.connection.execute(
                """
                UPDATE service_submissions
                SET operation_count = ?, operations_json = ?, error = ?,
                    updated_at = ?
                WHERE submission_id = ?
                """,
                (
                    len(operations),
                    payload,
                    "partially discarded by destructive scope",
                    time.time(),
                    submission_id,
                ),
            )

    def _discard_queued_submissions_for_writer(
        self, user_id: str, writer_id: str
    ) -> int:
        rows = self._queued_service_submissions(user_id, writer_id=writer_id)
        for row in rows:
            self._discard_service_submission(row["submission_id"])
        return len(rows)

    def _discard_queued_submissions_for_user(self, user_id: str) -> int:
        rows = self._queued_service_submissions(user_id)
        for row in rows:
            self._discard_service_submission(row["submission_id"])
        return len(rows)

    def _delete_queued_submissions_for_user(self, user_id: str) -> int:
        rows = self._queued_service_submissions(user_id)
        with self._store.connection:
            self._store.connection.execute(
                """
                DELETE FROM service_submissions
                WHERE user_id = ? AND state = ?
                """,
                (user_id, SubmissionState.QUEUED.value),
            )
        return len(rows)

    def _queued_service_submissions(
        self,
        user_id: str,
        *,
        writer_id: str | None = None,
    ) -> list:
        if writer_id is None:
            return self._store.connection.execute(
                """
                SELECT submission_id, operations_json FROM service_submissions
                WHERE user_id = ? AND state = ?
                """,
                (user_id, SubmissionState.QUEUED.value),
            ).fetchall()
        return self._store.connection.execute(
            """
            SELECT submission_id, operations_json FROM service_submissions
            WHERE user_id = ? AND writer_id = ? AND state = ?
            """,
            (user_id, writer_id, SubmissionState.QUEUED.value),
        ).fetchall()

    def _discard_service_submission(self, submission_id: str) -> None:
        with self._store.connection:
            self._store.connection.execute(
                """
                UPDATE service_submissions
                SET state = ?, error = ?, updated_at = ?
                WHERE submission_id = ?
                """,
                (
                    SubmissionState.DISCARDED.value,
                    "discarded by destructive scope",
                    time.time(),
                    submission_id,
                ),
            )

    def _operation_targets_prefix(
        self,
        operation: CtxfsOperation,
        prefix: str,
        source_keys: list[str],
    ) -> bool:
        if operation.kind in (OperationKind.PUT, OperationKind.MOVE) and (
            _path_is_within_prefix(operation.path, prefix)
        ):
            return True
        if operation.kind == OperationKind.DECISION and operation.path is not None:
            return _path_is_within_prefix(operation.path, prefix)
        if operation.kind in (OperationKind.DELETE, OperationKind.MOVE):
            return operation.source_key in source_keys
        return False

    def _record_deletion(self, result: DeletionResult) -> None:
        with self._store.connection:
            self._store.connection.execute(
                """
                INSERT INTO deletion_records(
                    deletion_id, scope, user_id, writer_id, prefix, receipt,
                    entry_count, decision_count, queued_submission_count,
                    writer_registration_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    result.scope,
                    result.user_id,
                    result.writer_id,
                    result.prefix,
                    result.receipt,
                    result.entry_count,
                    result.decision_count,
                    result.queued_submission_count,
                    result.writer_registration_count,
                    time.time(),
                ),
            )

    def _assert_user_removed(self, user_id: str) -> None:
        tables = (
            "entries",
            "decisions",
            "submissions",
            "service_submissions",
            "writer_prefixes",
        )
        for table in tables:
            row = self._store.connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row["count"] != 0:
                raise RuntimeError(f"ctxfs delete_user left rows in {table}")
        if self._store._user_root(user_id).exists():
            raise RuntimeError("ctxfs delete_user left tree files")
        if (self._store.objects_root / _disk_name(user_id)).exists():
            raise RuntimeError("ctxfs delete_user left object files")

    def _normalize_prefix(self, prefix: str) -> str:
        return _normalize_path(prefix)


def open_ctxfs_service(root: Path) -> CtxfsService:
    service = CtxfsService(root)
    service.preflight()
    return service
