from __future__ import annotations

import hashlib
import os
import time

import pytest
from ctxfs import (
    ContentHashMismatchError,
    CtxfsStore,
    DecisionOperation,
    DecisionState,
    DeleteOperation,
    DocClass,
    MoveOperation,
    PrefixAuthorizationError,
    Provenance,
    PutOperation,
    SubmissionState,
)
from ctxfs.store import GREP_MAX_JSON_RECORD_BYTES


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


def test_move_preserves_source_identity_and_object_without_rewriting(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")

    store.submit(
        "user-1",
        "writer-1",
        [_put("local_files/root1/a/notes.md", "inode-1", "alpha\nneedle\n")],
    )
    before = store.stat("user-1", "local_files/root1/a/notes.md")
    assert before is not None

    submission = store.submit(
        "user-1",
        "writer-1",
        [
            MoveOperation(
                source_key="inode-1",
                path="local_files/root1/b/notes.md",
                source_version="v2",
                expected_content_hash=before.content_hash,
            )
        ],
    )

    assert submission.state == "applied"
    assert store.stat("user-1", "local_files/root1/a/notes.md") is None
    after = store.stat("user-1", "local_files/root1/b/notes.md")
    assert after is not None
    assert after.source_key == before.source_key
    assert after.object_id == before.object_id
    assert after.content_hash == before.content_hash
    assert after.source_version == "v2"
    assert store.read("user-1", "local_files/root1/b/notes.md").text == (
        "alpha\nneedle\n"
    )


def test_writer_status_summary_exposes_submission_and_catalog_health(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local-files/app-1")
    store.submit(
        "user-1",
        "writer-1",
        [_put("local-files/app-1/notes.md", "inode-1", "alpha")],
    )
    now = time.time()
    with pytest.raises(KeyError):
        store.submit(
            "user-1",
            "writer-1",
            [
                MoveOperation(
                    source_key="missing-inode",
                    path="local-files/app-1/missing.md",
                    source_version="v2",
                    expected_content_hash="sha256:" + "a" * 64,
                )
            ],
        )
    with store.connection:
        store.connection.execute(
            """
            INSERT INTO submissions(
                submission_id, user_id, writer_id, state, operation_count,
                error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                "queued-submission",
                "user-1",
                "writer-1",
                SubmissionState.QUEUED.value,
                1,
                now - 30,
                now - 30,
            ),
        )
    entry = store.stat("user-1", "local-files/app-1/notes.md")
    assert entry is not None
    store._tree_path("user-1", entry.path).unlink()

    summary = store.writer_status_summary(
        "user-1",
        "writer-1",
        prefix="local-files/app-1",
        now=now,
    )

    assert summary.submission_state_counts[SubmissionState.APPLIED] == 1
    assert summary.submission_state_counts[SubmissionState.FAILED] == 1
    assert summary.submission_state_counts[SubmissionState.QUEUED] == 1
    assert summary.queue_depth == 1
    assert summary.oldest_unapplied_submission_age_seconds == 30
    assert summary.entry_count == 1
    assert summary.incomplete_entry_count == 1


def test_writer_status_summary_counts_current_decisions(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local-files/app-1")

    store.submit(
        "user-1",
        "writer-1",
        [
            DecisionOperation(
                source_key="file-1",
                source_version="v1",
                path="local-files/app-1/notes.md",
                state=DecisionState.UPLOAD_REQUIRED,
            ),
            DecisionOperation(
                source_key="file-2",
                source_version="v1",
                path="local-files/app-1/archive.md",
                state=DecisionState.TERMINAL_FAILURE,
                failure_code="pdf_parse_failed",
                failure_message="PDF parsing failed",
            ),
        ],
    )
    store.submit(
        "user-1",
        "writer-1",
        [
            DecisionOperation(
                source_key="file-1",
                source_version="v2",
                path="local-files/app-1/notes.md",
                state=DecisionState.STORED,
            )
        ],
    )

    summary = store.writer_status_summary(
        "user-1",
        "writer-1",
        prefix="local-files/app-1",
    )

    assert summary.decision_state_counts == {
        DecisionState.STORED: 1,
        DecisionState.TERMINAL_FAILURE: 1,
    }


def test_exact_reads_return_hashes_and_completeness(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    store.submit(
        "user-1",
        "writer-1",
        [_put("local_files/root1/notes.md", "inode-1", "alpha\nneedle beta\n")],
    )

    entry = store.stat("user-1", "local_files/root1/notes.md")
    assert entry is not None

    read = store.read("user-1", "local_files/root1/notes.md")
    assert read.text == "alpha\nneedle beta\n"
    assert read.content_hash == entry.content_hash
    assert read.complete is True

    lines = store.read_lines("user-1", "local_files/root1/notes.md", 2, 4)
    assert lines.lines == ["needle beta"]
    assert lines.content_hash == entry.content_hash
    assert lines.complete is False
    assert lines.stopped_by == "eof"

    matches = store.grep("user-1", "needle", prefix="local_files/root1")
    assert matches.complete is True
    assert len(matches.items) == 1
    assert matches.items[0].path == "local_files/root1/notes.md"
    assert matches.items[0].match_start == 0
    assert matches.items[0].match_end == len("needle")
    assert matches.items[0].content_hash == entry.content_hash


def test_tree_and_glob_return_bounded_catalog_entries(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    store.submit(
        "user-1",
        "writer-1",
        [
            _put("local_files/root1/a/notes.md", "inode-1", "alpha"),
            _put("local_files/root1/b/report.txt", "inode-2", "beta"),
        ],
    )

    tree = store.tree("user-1", "local_files/root1", limit=1)
    assert [(item.path, item.kind) for item in tree.items] == [
        ("local_files/root1/a", "directory")
    ]
    assert tree.complete is False
    assert tree.stopped_by == "limit"

    full_tree = store.tree("user-1", "local_files/root1")
    assert [(item.path, item.kind) for item in full_tree.items] == [
        ("local_files/root1/a", "directory"),
        ("local_files/root1/a/notes.md", "file"),
        ("local_files/root1/b", "directory"),
        ("local_files/root1/b/report.txt", "file"),
    ]
    assert full_tree.complete is True

    shallow_tree = store.tree("user-1", "local_files/root1", depth=1)
    assert [(item.path, item.kind) for item in shallow_tree.items] == [
        ("local_files/root1/a", "directory"),
        ("local_files/root1/b", "directory"),
    ]
    assert shallow_tree.complete is True

    matches = store.glob("user-1", "*.md", prefix="local_files/root1")
    assert matches.items == []
    assert matches.complete is True

    nested_matches = store.glob("user-1", "**/*.md", prefix="local_files/root1")
    assert [item.path for item in nested_matches.items] == [
        "local_files/root1/a/notes.md"
    ]
    assert nested_matches.complete is True

    anchored_matches = store.glob("user-1", "local_files/root1/**/*.md")
    assert [item.path for item in anchored_matches.items] == [
        "local_files/root1/a/notes.md"
    ]
    assert anchored_matches.complete is True


def test_tree_and_glob_skip_missing_tree_paths(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    path = "local_files/root1/a/notes.md"
    store.submit("user-1", "writer-1", [_put(path, "inode-1", "alpha")])
    store._tree_path("user-1", path).unlink()

    assert store.stat("user-1", path) is None
    tree = store.tree("user-1", "local_files/root1")
    assert tree.items == []
    assert tree.complete is False
    assert tree.stopped_by == "catalog_mismatch"
    matches = store.glob("user-1", "**/*.md", prefix="local_files/root1")
    assert matches.items == []
    assert matches.complete is False
    assert matches.stopped_by == "catalog_mismatch"


def test_tree_glob_and_grep_surface_disk_only_paths_as_incomplete(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    path = "local_files/root1/orphan.md"
    tree_path = store._tree_path("user-1", path)
    tree_path.parent.mkdir(parents=True)
    tree_path.write_text("needle\n", encoding="utf-8")

    tree = store.tree("user-1", "local_files/root1")
    assert [(item.path, item.kind, item.content_hash) for item in tree.items] == [
        ("local_files/root1/orphan.md", "file", None)
    ]
    assert tree.complete is False
    assert tree.stopped_by == "catalog_mismatch"

    matches = store.glob("user-1", "**/*.md", prefix="local_files/root1")
    assert [item.path for item in matches.items] == [path]
    assert matches.complete is False
    assert matches.stopped_by == "catalog_mismatch"

    grep = store.grep("user-1", "needle", prefix="local_files/root1")
    expected_hash = "sha256:" + hashlib.sha256(b"needle\n").hexdigest()
    assert [(item.path, item.content_hash) for item in grep.items] == [
        (path, expected_hash)
    ]
    assert grep.complete is False
    assert grep.stopped_by == "catalog_mismatch"


@pytest.mark.parametrize("pattern", ["notes*/", "[", "a**b"])
def test_glob_rejects_invalid_patterns(tmp_path, pattern):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    store.submit(
        "user-1",
        "writer-1",
        [_put("local_files/root1/notes.md", "inode-1", "alpha")],
    )

    with pytest.raises(ValueError, match="invalid ctxfs glob pattern"):
        store.glob("user-1", pattern, prefix="local_files/root1")


@pytest.mark.parametrize(
    "bad_path",
    ["local_files/root/./notes.md", "local_files/root//notes.md", "C:/notes.md"],
)
def test_paths_reject_invalid_components_before_normalization(tmp_path, bad_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")

    with pytest.raises(ValueError, match="invalid ctxfs path"):
        store.submit("user-1", "writer-1", [_put(bad_path, "inode-1", "content")])

    assert store.ls("user-1", "local_files/root").items == []


def test_root_glob_is_anchored_to_user_root(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    store.submit(
        "user-1",
        "writer-1",
        [_put("local_files/root1/a/notes.md", "inode-1", "alpha")],
    )

    matches = store.glob("user-1", "*.md")
    assert matches.items == []

    matches = store.glob("user-1", "**/*.md")
    assert [item.path for item in matches.items] == ["local_files/root1/a/notes.md"]
    assert matches.complete is True


def test_disk_paths_preserve_case_and_unicode_distinct_logical_paths(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    store.submit(
        "user-1",
        "writer-1",
        [
            _put("local_files/root/Notes.md", "inode-1", "UPPER"),
            _put("local_files/root/notes.md", "inode-2", "lower"),
            _put("local_files/root/\u00e9.md", "inode-3", "nfc"),
            _put("local_files/root/e\u0301.md", "inode-4", "nfd"),
        ],
    )

    assert store.read("user-1", "local_files/root/Notes.md").text == "UPPER"
    assert store.read("user-1", "local_files/root/notes.md").text == "lower"
    assert store.read("user-1", "local_files/root/\u00e9.md").text == "nfc"
    assert store.read("user-1", "local_files/root/e\u0301.md").text == "nfd"


def test_truncated_read_hash_stays_on_full_document(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    path = "local_files/root1/notes.md"
    store.submit("user-1", "writer-1", [_put(path, "inode-1", "éx")])
    entry = store.stat("user-1", path)
    assert entry is not None

    read = store.read("user-1", path, max_bytes=1)

    assert read.text == ""
    assert read.complete is False
    assert read.stopped_by == "max_bytes"
    assert read.content_hash == entry.content_hash


def test_complete_read_rejects_catalog_hash_mismatch(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    path = "local_files/root1/notes.md"
    store.submit("user-1", "writer-1", [_put(path, "inode-1", "original")])
    store._tree_path("user-1", path).write_text(
        "changed",
        encoding="utf-8",
    )

    with pytest.raises(ContentHashMismatchError, match="expected sha256:"):
        store.read("user-1", path)


def test_truncated_read_rejects_catalog_hash_mismatch(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    path = "local_files/root1/notes.md"
    store.submit("user-1", "writer-1", [_put(path, "inode-1", "original")])
    store._tree_path("user-1", path).write_text(
        "changed",
        encoding="utf-8",
    )

    with pytest.raises(ContentHashMismatchError, match="expected sha256:"):
        store.read("user-1", path, max_bytes=1)


def test_read_lines_rejects_catalog_hash_mismatch(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    path = "local_files/root1/notes.md"
    store.submit("user-1", "writer-1", [_put(path, "inode-1", "original\n")])
    store._tree_path("user-1", path).write_text(
        "changed\n",
        encoding="utf-8",
    )

    with pytest.raises(ContentHashMismatchError, match="expected sha256:"):
        store.read_lines("user-1", path, 1, 1)


def test_prefix_authorization_is_component_terminated(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")

    with pytest.raises(PrefixAuthorizationError):
        store.submit(
            "user-1",
            "writer-1",
            [_put("local_files/root10/notes.md", "inode-1", "wrong root")],
        )

    assert store.stat("user-1", "local_files/root10/notes.md") is None


@pytest.mark.parametrize("bad_character", ["\n", "\t", "\x00", "\x7f"])
def test_paths_reject_control_characters_before_filesystem_access(
    tmp_path,
    bad_character,
):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path = f"local_files/root/bad{bad_character}name.md"

    with pytest.raises(ValueError, match="invalid ctxfs path"):
        store.submit("user-1", "writer-1", [_put(path, "inode-1", "content")])

    assert store.ls("user-1", "local_files/root").items == []


def test_register_prefix_rejects_overlapping_prefixes_for_different_writers(
    tmp_path,
):
    parent_first = CtxfsStore(tmp_path / "parent-first")
    parent_first.register_prefix("user-1", "writer-a", "local_files/root")
    with pytest.raises(PrefixAuthorizationError):
        parent_first.register_prefix(
            "user-1",
            "writer-b",
            "local_files/root/nested",
        )

    child_first = CtxfsStore(tmp_path / "child-first")
    child_first.register_prefix("user-1", "writer-a", "local_files/root/nested")
    with pytest.raises(PrefixAuthorizationError):
        child_first.register_prefix("user-1", "writer-b", "local_files/root")


def test_register_prefix_is_idempotent_for_same_writer(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")

    store.register_prefix("user-1", "writer-1", "local_files/root")
    store.register_prefix("user-1", "writer-1", "local_files/root")

    store.submit(
        "user-1",
        "writer-1",
        [_put("local_files/root/notes.md", "inode-1", "alpha")],
    )

    assert store.read("user-1", "local_files/root/notes.md").text == "alpha"


def test_source_key_identity_is_scoped_by_writer(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "drive-a", "local_files/drive-a")
    store.register_prefix("user-1", "drive-b", "local_files/drive-b")

    store.submit(
        "user-1",
        "drive-a",
        [_put("local_files/drive-a/notes.md", "file-123", "account a")],
    )
    store.submit(
        "user-1",
        "drive-b",
        [_put("local_files/drive-b/notes.md", "file-123", "account b")],
    )

    assert store.read("user-1", "local_files/drive-a/notes.md").text == "account a"
    assert store.read("user-1", "local_files/drive-b/notes.md").text == "account b"
    drive_a_before = store.stat("user-1", "local_files/drive-a/notes.md")
    assert drive_a_before is not None

    store.submit(
        "user-1",
        "drive-a",
        [
            MoveOperation(
                source_key="file-123",
                path="local_files/drive-a/moved.md",
                source_version="v2",
                expected_content_hash=drive_a_before.content_hash,
            )
        ],
    )

    assert store.stat("user-1", "local_files/drive-a/notes.md") is None
    assert store.read("user-1", "local_files/drive-a/moved.md").text == "account a"
    assert store.read("user-1", "local_files/drive-b/notes.md").text == "account b"

    store.submit(
        "user-1",
        "drive-a",
        [DeleteOperation(source_key="file-123", source_version="v3")],
    )

    assert store.stat("user-1", "local_files/drive-a/moved.md") is None
    assert store.ls("user-1", "local_files/drive-a").items == []
    assert store.read("user-1", "local_files/drive-b/notes.md").text == "account b"


def test_move_with_stale_expected_hash_preserves_source(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    source_path = "local_files/root/source.md"
    destination_path = "local_files/root/moved.md"
    store.submit("user-1", "writer-1", [_put(source_path, "inode-1", "current")])
    before = store.stat("user-1", source_path)
    assert before is not None

    with pytest.raises(ContentHashMismatchError):
        store.submit(
            "user-1",
            "writer-1",
            [
                MoveOperation(
                    source_key="inode-1",
                    path=destination_path,
                    source_version="v2",
                    expected_content_hash=f"sha256:{hashlib.sha256(b'stale').hexdigest()}",
                )
            ],
        )

    after = store.stat("user-1", source_path)
    assert after is not None
    assert after.content_hash == before.content_hash
    assert store.stat("user-1", destination_path) is None
    assert store.read("user-1", source_path).text == "current"


def test_replayed_move_rekeys_after_tree_rename_already_landed(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    source_path = "local_files/root/source.md"
    destination_path = "local_files/root/moved.md"
    store.submit("user-1", "writer-1", [_put(source_path, "inode-1", "current")])
    before = store.stat("user-1", source_path)
    assert before is not None

    store._rename_tree_entry("user-1", source_path, destination_path, [], [])

    submission = store.submit(
        "user-1",
        "writer-1",
        [
            MoveOperation(
                source_key="inode-1",
                path=destination_path,
                source_version="v2",
                expected_content_hash=before.content_hash,
            )
        ],
    )

    assert submission.state == "applied"
    assert store.stat("user-1", source_path) is None
    after = store.stat("user-1", destination_path)
    assert after is not None
    assert after.content_hash == before.content_hash
    assert after.source_version == "v2"
    assert store.read("user-1", destination_path).text == "current"


def test_replayed_move_rekeys_when_destination_was_superseded(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    source_path = "local_files/root/source.md"
    destination_path = "local_files/root/moved.md"
    store.submit("user-1", "writer-1", [_put(source_path, "inode-1", "current")])
    before = store.stat("user-1", source_path)
    assert before is not None
    store._tree_path("user-1", source_path).unlink()
    destination = store._tree_path("user-1", destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("newer\n", encoding="utf-8")

    submission = store.submit(
        "user-1",
        "writer-1",
        [
            MoveOperation(
                source_key="inode-1",
                path=destination_path,
                source_version="v2",
                expected_content_hash=before.content_hash,
            )
        ],
    )

    assert submission.state == "applied"
    after = store.stat("user-1", destination_path)
    assert after is not None
    assert after.source_version == "v2"


def test_failed_batch_does_not_leave_uncataloged_file(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")

    with pytest.raises(PrefixAuthorizationError):
        store.submit(
            "user-1",
            "writer-1",
            [
                _put("local_files/root1/kept-on-disk.txt", "inode-1", "valid"),
                _put("local_files/root2/rejected.txt", "inode-2", "rejected"),
            ],
        )

    assert store.stat("user-1", "local_files/root1/kept-on-disk.txt") is None
    assert store.ls("user-1", "local_files/root1").items == []
    with pytest.raises(FileNotFoundError):
        store.read("user-1", "local_files/root1/kept-on-disk.txt")


def test_put_to_occupied_path_preserves_original_entry(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    path = "local_files/root1/notes.md"
    store.submit("user-1", "writer-1", [_put(path, "inode-1", "original")])
    before = store.stat("user-1", path)
    assert before is not None

    with pytest.raises(FileExistsError):
        store.submit("user-1", "writer-1", [_put(path, "inode-2", "replacement")])

    after = store.stat("user-1", path)
    assert after is not None
    assert after.source_key == "inode-1"
    assert after.content_hash == before.content_hash
    assert store.read("user-1", path).text == "original"
    listing = store.ls("user-1", "local_files/root1")
    assert [(item.path, item.content_hash) for item in listing.items] == [
        (path, before.content_hash)
    ]


def test_move_to_occupied_path_preserves_source_and_destination(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root1")
    source_path = "local_files/root1/source.md"
    destination_path = "local_files/root1/destination.md"
    store.submit(
        "user-1",
        "writer-1",
        [
            _put(source_path, "inode-1", "source"),
            _put(destination_path, "inode-2", "destination"),
        ],
    )
    source_before = store.stat("user-1", source_path)
    destination_before = store.stat("user-1", destination_path)
    assert source_before is not None
    assert destination_before is not None

    with pytest.raises(FileExistsError):
        store.submit(
            "user-1",
            "writer-1",
            [
                MoveOperation(
                    source_key="inode-1",
                    path=destination_path,
                    source_version="v2",
                    expected_content_hash=source_before.content_hash,
                )
            ],
        )

    source_after = store.stat("user-1", source_path)
    destination_after = store.stat("user-1", destination_path)
    assert source_after is not None
    assert destination_after is not None
    assert source_after.content_hash == source_before.content_hash
    assert destination_after.content_hash == destination_before.content_hash
    assert store.read("user-1", source_path).text == "source"
    assert store.read("user-1", destination_path).text == "destination"


def test_grep_skips_missing_tree_paths(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path = "local_files/root/notes.md"
    store.submit("user-1", "writer-1", [_put(path, "inode-1", "needle")])
    store._tree_path("user-1", path).unlink()

    matches = store.grep("user-1", "needle", prefix="local_files/root")

    assert matches.items == []
    assert matches.complete is False
    assert matches.stopped_by == "catalog_mismatch"


def test_grep_pathological_regex_does_not_backtrack_indefinitely(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    store.submit(
        "user-1",
        "writer-1",
        [_put("local_files/root/notes.md", "inode-1", f"{'a' * 30}!\n")],
    )

    started = time.monotonic()
    matches = store.grep("user-1", "(a+)+$", prefix="local_files/root")
    elapsed = time.monotonic() - started

    assert matches.items == []
    assert matches.complete is False
    assert matches.stopped_by == "timeout"
    assert elapsed < 1.0


def test_put_update_removes_superseded_object(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path = "local_files/root/notes.md"

    store.submit("user-1", "writer-1", [_put(path, "inode-1", "alpha")])
    first = store.stat("user-1", path)
    assert first is not None
    first_object = store._object_path("user-1", first.object_id)
    assert first_object.exists()

    store.submit("user-1", "writer-1", [_put(path, "inode-1", "beta")])
    second = store.stat("user-1", path)

    assert second is not None
    assert second.object_id != first.object_id
    assert store._object_path("user-1", second.object_id).exists()
    assert not first_object.exists()


def test_grep_invalid_utf8_match_offsets_slice_returned_line(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path = "local_files/root/notes.md"
    store.submit(
        "user-1",
        "writer-1",
        [_put(path, "inode-1", "placeholder")],
    )
    store._tree_path("user-1", path).write_bytes(b"be\xffta needle\n")

    matches = store.grep("user-1", "needle", prefix="local_files/root")

    assert len(matches.items) == 1
    match = matches.items[0]
    assert match.line == "be\ufffdta needle"
    assert match.line[match.match_start : match.match_end] == "needle"


def test_grep_returns_limit_without_waiting_for_rg_eof(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_rg = fake_bin / "rg"
    fake_rg.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time

path = sys.argv[-1]
if os.path.basename(path) == "null":
    raise SystemExit(1)

with open(path, encoding="utf-8") as handle:
    for line_number, line in enumerate(handle, start=1):
        payload = {
            "type": "match",
            "data": {
                "line_number": line_number,
                "lines": {"text": line},
                "submatches": [{"start": 0, "end": 6}],
            },
        }
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()
        if line_number == 3:
            time.sleep(5)
""",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    store.submit(
        "user-1",
        "writer-1",
        [
            _put(
                "local_files/root/notes.md",
                "inode-1",
                "".join(f"needle {line_number}\n" for line_number in range(1000)),
            )
        ],
    )

    started = time.monotonic()
    matches = store.grep("user-1", ".", prefix="local_files/root", limit=3)
    elapsed = time.monotonic() - started

    assert matches.complete is False
    assert matches.stopped_by == "limit"
    assert [match.line_number for match in matches.items] == [1, 2, 3]
    assert elapsed < 1.0


def test_grep_stops_on_oversized_rg_json_record(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_rg = fake_bin / "rg"
    fake_rg.write_text(
        f"""#!/usr/bin/env python3
import os
import sys
import time

path = sys.argv[-1]
if os.path.basename(path) == "null":
    raise SystemExit(1)

sys.stdout.buffer.write(b"{{" * {GREP_MAX_JSON_RECORD_BYTES + 1})
sys.stdout.buffer.flush()
time.sleep(5)
""",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    store.submit(
        "user-1",
        "writer-1",
        [_put("local_files/root/notes.md", "inode-1", "needle\n")],
    )

    started = time.monotonic()
    matches = store.grep("user-1", "needle", prefix="local_files/root", limit=3)
    elapsed = time.monotonic() - started

    assert matches.items == []
    assert matches.complete is False
    assert matches.stopped_by == "max_bytes"
    assert elapsed < 1.0


def test_dependent_move_batch_vacates_destinations_before_filling(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path_a = "local_files/root/a.md"
    path_b = "local_files/root/b.md"
    path_c = "local_files/root/c.md"
    store.submit(
        "user-1",
        "writer-1",
        [
            _put(path_a, "inode-a", "alpha"),
            _put(path_b, "inode-b", "beta"),
        ],
    )
    entry_a = store.stat("user-1", path_a)
    entry_b = store.stat("user-1", path_b)
    assert entry_a is not None
    assert entry_b is not None

    submission = store.submit(
        "user-1",
        "writer-1",
        [
            MoveOperation(
                source_key="inode-a",
                path=path_b,
                source_version="v2",
                expected_content_hash=entry_a.content_hash,
            ),
            MoveOperation(
                source_key="inode-b",
                path=path_c,
                source_version="v2",
                expected_content_hash=entry_b.content_hash,
            ),
        ],
    )

    assert submission.state == "applied"
    assert store.stat("user-1", path_a) is None
    assert store.read("user-1", path_b).text == "alpha"
    assert store.read("user-1", path_c).text == "beta"


def test_move_cycle_uses_temporary_path(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path_a = "local_files/root/a.md"
    path_b = "local_files/root/b.md"
    store.submit(
        "user-1",
        "writer-1",
        [
            _put(path_a, "inode-a", "alpha"),
            _put(path_b, "inode-b", "beta"),
        ],
    )
    entry_a = store.stat("user-1", path_a)
    entry_b = store.stat("user-1", path_b)
    assert entry_a is not None
    assert entry_b is not None

    submission = store.submit(
        "user-1",
        "writer-1",
        [
            MoveOperation(
                source_key="inode-a",
                path=path_b,
                source_version="v2",
                expected_content_hash=entry_a.content_hash,
            ),
            MoveOperation(
                source_key="inode-b",
                path=path_a,
                source_version="v2",
                expected_content_hash=entry_b.content_hash,
            ),
        ],
    )

    assert submission.state == "applied"
    assert store.read("user-1", path_a).text == "beta"
    assert store.read("user-1", path_b).text == "alpha"


def test_duplicate_move_source_key_fails_before_mutation(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    source_path = "local_files/root/source.md"
    first_path = "local_files/root/first.md"
    second_path = "local_files/root/second.md"
    store.submit("user-1", "writer-1", [_put(source_path, "inode-1", "original")])
    entry = store.stat("user-1", source_path)
    assert entry is not None

    with pytest.raises(ValueError, match="duplicate move source_key"):
        store.submit(
            "user-1",
            "writer-1",
            [
                MoveOperation(
                    source_key="inode-1",
                    path=first_path,
                    source_version="v2",
                    expected_content_hash=entry.content_hash,
                ),
                MoveOperation(
                    source_key="inode-1",
                    path=second_path,
                    source_version="v3",
                    expected_content_hash=entry.content_hash,
                ),
            ],
        )

    assert store.read("user-1", source_path).text == "original"
    assert store.stat("user-1", first_path) is None
    assert store.stat("user-1", second_path) is None


def test_deletes_and_moves_run_before_puts(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    source_path = "local_files/root/source.md"
    replacement_path = "local_files/root/replacement.md"
    store.submit(
        "user-1",
        "writer-1",
        [_put(source_path, "inode-old", "old")],
    )
    old = store.stat("user-1", source_path)
    assert old is not None

    submission = store.submit(
        "user-1",
        "writer-1",
        [
            _put(source_path, "inode-new", "new"),
            MoveOperation(
                source_key="inode-old",
                path=replacement_path,
                source_version="v2",
                expected_content_hash=old.content_hash,
            ),
        ],
    )

    assert submission.state == "applied"
    assert store.read("user-1", source_path).text == "new"
    assert store.read("user-1", replacement_path).text == "old"


def test_put_rejects_nul_text_without_tree_or_catalog_entry(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path = "local_files/root/notes.md"

    with pytest.raises(ValueError, match="NUL"):
        store.submit("user-1", "writer-1", [_put(path, "inode-1", "alpha\x00beta")])

    assert store.stat("user-1", path) is None
    assert store.ls("user-1", "local_files/root").items == []
    with pytest.raises(FileNotFoundError):
        store.read("user-1", path)


def test_put_normalizes_line_endings_before_storing_and_hashing(tmp_path):
    store = CtxfsStore(tmp_path / "ctxfs")
    store.register_prefix("user-1", "writer-1", "local_files/root")
    path = "local_files/root/notes.md"
    normalized_text = "alpha\nbeta\ngamma\n"

    store.submit(
        "user-1",
        "writer-1",
        [_put(path, "inode-1", "alpha\r\nbeta\rgamma\r\n")],
    )
    crlf_entry = store.stat("user-1", path)
    assert crlf_entry is not None
    assert store.read("user-1", path).text == normalized_text

    store.submit("user-1", "writer-1", [_put(path, "inode-1", normalized_text)])
    lf_entry = store.stat("user-1", path)
    assert lf_entry is not None

    assert lf_entry.content_hash == crlf_entry.content_hash
    assert lf_entry.size_bytes == crlf_entry.size_bytes
    assert store.read("user-1", path).text == normalized_text
