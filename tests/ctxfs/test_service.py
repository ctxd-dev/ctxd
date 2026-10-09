from __future__ import annotations

import pytest
from ctxfs import (
    CtxfsService,
    DecisionOperation,
    DecisionState,
    DocClass,
    PrefixAuthorizationError,
    Provenance,
    PutOperation,
    SubmissionState,
    open_ctxfs_service,
)
from ctxfs.store import _disk_name


def _put(path: str, source_key: str, text: str) -> PutOperation:
    return PutOperation(
        path=path,
        source_key=source_key,
        source_version="v1",
        doc_class=DocClass.EDITABLE,
        file_format="text/markdown",
        text=text,
        provenance=Provenance(producer="test", producer_version="1"),
    )


def _decision(path: str, source_key: str) -> DecisionOperation:
    return DecisionOperation(
        source_key=source_key,
        source_version="v1",
        state=DecisionState.STORED,
        path=path,
    )


def test_write_service_accepts_durable_queue_before_applier_publishes_to_reads(
    tmp_path,
):
    service = CtxfsService(tmp_path / "ctxfs")
    service.write.register_prefix("user-1", "writer-1", "local-files/app-1")

    queued = service.write.submit(
        "user-1",
        "writer-1",
        [_put("local-files/app-1/notes.md", "file-1", "alpha\n")],
    )

    assert queued.state == SubmissionState.QUEUED
    assert service.maintainer.queue_depth() == 1
    with pytest.raises(FileNotFoundError):
        service.read.read("user-1", "local-files/app-1/notes.md")
    service.close()

    reopened = open_ctxfs_service(tmp_path / "ctxfs")
    assert reopened.maintainer.queue_depth() == 1
    applied = reopened.applier.apply_next()

    assert applied is not None
    assert applied.submission_id == queued.submission_id
    assert applied.state == SubmissionState.APPLIED
    assert reopened.maintainer.queue_depth() == 0
    assert reopened.read.read("user-1", "local-files/app-1/notes.md").text == (
        "alpha\n"
    )


def test_applier_records_failed_submission_and_continues_to_later_work(tmp_path):
    service = CtxfsService(tmp_path / "ctxfs")
    service.write.register_prefix("user-1", "writer-1", "local-files/app-1")
    failed = service.write.submit(
        "user-1",
        "writer-1",
        [_put("other-prefix/notes.md", "bad-file", "not allowed")],
    )
    queued = service.write.submit(
        "user-1",
        "writer-1",
        [_put("local-files/app-1/notes.md", "file-1", "alpha")],
    )

    first, second = service.applier.drain(limit=2)

    assert first.submission_id == failed.submission_id
    assert first.state == SubmissionState.FAILED
    assert first.error is not None
    assert "not authorized" in first.error
    assert second.submission_id == queued.submission_id
    assert second.state == SubmissionState.APPLIED
    assert service.read.read("user-1", "local-files/app-1/notes.md").text == "alpha"


def test_write_service_keeps_prefix_authorization_on_registration(tmp_path):
    service = CtxfsService(tmp_path / "ctxfs")
    service.write.register_prefix("user-1", "writer-a", "local-files/root")

    with pytest.raises(PrefixAuthorizationError):
        service.write.register_prefix("user-1", "writer-b", "local-files/root/nested")


def test_management_delete_prefix_removes_subtree_and_discards_matching_queue(
    tmp_path,
):
    service = CtxfsService(tmp_path / "ctxfs")
    service.write.register_prefix("user-1", "writer-1", "local-files/app-1")
    service.write.submit(
        "user-1",
        "writer-1",
        [
            _put("local-files/app-1/remove/a.md", "remove-a", "remove"),
            _put("local-files/app-1/keep/b.md", "keep-b", "keep"),
            _decision("local-files/app-1/remove/a.md", "remove-a"),
        ],
    )
    service.applier.drain()
    service.write.submit(
        "user-1",
        "writer-1",
        [
            _put("local-files/app-1/remove/c.md", "remove-c", "queued"),
            _put("local-files/app-1/keep/d.md", "keep-d", "queued"),
        ],
    )

    result = service.management.delete_prefix(
        "user-1",
        "writer-1",
        "local-files/app-1/remove",
    )

    assert result.entry_count == 1
    assert result.decision_count == 1
    assert result.queued_submission_count == 1
    with pytest.raises(FileNotFoundError):
        service.read.read("user-1", "local-files/app-1/remove/a.md")
    assert service.read.read("user-1", "local-files/app-1/keep/b.md").text == "keep"
    assert service.maintainer.queue_depth() == 1
    applied = service.applier.apply_next()
    assert applied is not None
    assert applied.state == SubmissionState.APPLIED
    assert service.read.read("user-1", "local-files/app-1/keep/d.md").text == "queued"


def test_management_delete_integration_removes_writer_and_keeps_sibling(tmp_path):
    service = CtxfsService(tmp_path / "ctxfs")
    service.write.register_prefix("user-1", "writer-a", "local-files/app-a")
    service.write.register_prefix("user-1", "writer-b", "local-files/app-b")
    service.write.submit(
        "user-1",
        "writer-a",
        [_put("local-files/app-a/doc.md", "a-doc", "a")],
    )
    service.write.submit(
        "user-1",
        "writer-b",
        [_put("local-files/app-b/doc.md", "b-doc", "b")],
    )
    service.applier.drain()
    service.write.submit(
        "user-1",
        "writer-a",
        [_put("local-files/app-a/queued.md", "a-queued", "queued")],
    )

    result = service.management.delete_integration(
        "user-1",
        "writer-a",
        receipt="disconnect-confirmed",
    )

    assert result.entry_count == 1
    assert result.queued_submission_count == 1
    assert result.writer_registration_count == 1
    with pytest.raises(FileNotFoundError):
        service.read.read("user-1", "local-files/app-a/doc.md")
    assert service.read.read("user-1", "local-files/app-b/doc.md").text == "b"
    service.write.register_prefix("user-1", "writer-c", "local-files/app-a")
    row = service._store.connection.execute(
        """
        SELECT receipt FROM deletion_records
        WHERE scope = ? AND user_id = ? AND writer_id = ?
        """,
        ("integration", "user-1", "writer-a"),
    ).fetchone()
    assert row["receipt"] == "disconnect-confirmed"


def test_management_delete_user_purges_catalog_queue_and_user_files(tmp_path):
    service = CtxfsService(tmp_path / "ctxfs")
    service.write.register_prefix("user-1", "writer-1", "local-files/app-1")
    service.write.register_prefix("user-2", "writer-2", "local-files/app-2")
    service.write.submit(
        "user-1",
        "writer-1",
        [_put("local-files/app-1/doc.md", "u1-doc", "u1")],
    )
    service.write.submit(
        "user-2",
        "writer-2",
        [_put("local-files/app-2/doc.md", "u2-doc", "u2")],
    )
    service.applier.drain()
    service.write.submit(
        "user-1",
        "writer-1",
        [_put("local-files/app-1/queued.md", "u1-queued", "queued")],
    )

    with pytest.raises(ValueError):
        service.management.delete_user("user-1", receipt="")
    result = service.management.delete_user("user-1", receipt="delete-account")

    assert result.entry_count == 1
    assert result.queued_submission_count == 1
    assert result.writer_registration_count == 1
    assert not service._store._user_root("user-1").exists()
    assert not (service._store.objects_root / _disk_name("user-1")).exists()
    for table in (
        "entries",
        "decisions",
        "submissions",
        "service_submissions",
        "writer_prefixes",
    ):
        row = service._store.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE user_id = ?",
            ("user-1",),
        ).fetchone()
        assert row["count"] == 0
    assert service.read.read("user-2", "local-files/app-2/doc.md").text == "u2"


def test_open_ctxfs_service_fails_preflight_when_store_root_is_not_directory(
    tmp_path,
):
    root = tmp_path / "ctxfs"
    root.write_text("not a directory")

    with pytest.raises(FileExistsError):
        open_ctxfs_service(root)
