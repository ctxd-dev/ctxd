import hashlib
import json
from pathlib import Path

import pytest

from ctxd.folders import FolderConfig
from ctxd.tracker import TrackerPaths, _submit_operations, run_forever, sync_once


def test_tracker_http_submission_sends_ctxfs_endpoint_credential(monkeypatch) -> None:
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, *, json, headers):
            captured.update(path=path, payload=json, headers=headers)
            return Response()

    monkeypatch.setattr(
        "ctxd.tracker.resolve_ctxfs_api_key", lambda **kwargs: "writer-token"
    )
    monkeypatch.setattr("ctxd.tracker.httpx.Client", Client)

    _submit_operations(
        "https://ctxfs.example",
        writer_id="writer",
        writer_prefix="Workstation",
        operations=[],
    )

    assert captured["headers"] == {"Authorization": "Bearer writer-token"}
    assert captured["path"] == "/api/ctxfs/submissions"


def test_tracker_sync_submits_text_files_and_persists_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    (docs / "README.md").write_text("hello ctxfs\n", encoding="utf-8")
    (docs / "image.bin").write_bytes(b"\xff\x00")
    folder = FolderConfig(
        name="Documents",
        path=str(docs),
        prefix="local-files/folder-1",
    )
    submissions = []

    def submit(endpoint, *, writer_id, writer_prefix, operations):
        submissions.append(
            {
                "endpoint": endpoint,
                "writer_id": writer_id,
                "writer_prefix": writer_prefix,
                "operations": operations,
            }
        )

    monkeypatch.setattr("ctxd.tracker._submit_operations", submit)

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=tmp_path / "state.json"),
    )

    assert result == {"folders": 1, "operations": 1}
    assert len(submissions) == 1
    assert submissions[0]["writer_id"] == "ctxd-tracker:local-files/folder-1"
    operation = submissions[0]["operations"][0]
    assert operation["kind"] == "put"
    assert operation["path"] == "local-files/folder-1/README.md"
    assert operation["text"] == "hello ctxfs\n"
    assert operation["file_format"] == "md"
    payload = json.loads((tmp_path / "state.json").read_text())
    tracked = next(iter(payload["local-files/folder-1"].values()))
    assert tracked == {
        "content_hash": operation["source_version"],
        "file_format": "md",
        "path": "local-files/folder-1/README.md",
        "source_version": operation["source_version"],
    }


def test_tracker_sync_submits_only_incremental_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    original = docs / "original.md"
    original.write_text("version one\n", encoding="utf-8")
    folder = FolderConfig(
        name="Documents",
        path=str(docs),
        prefix="local-files/folder-1",
    )
    paths = TrackerPaths(state_file=tmp_path / "state.json")
    submissions: list[list[dict]] = []

    def submit(endpoint, *, writer_id, writer_prefix, operations):
        del endpoint, writer_id, writer_prefix
        submissions.append(operations)

    monkeypatch.setattr("ctxd.tracker._submit_operations", submit)

    first = sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)
    assert first["operations"] == 1
    assert [operation["kind"] for operation in submissions.pop()] == ["put"]

    unchanged = sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)
    assert unchanged["operations"] == 0
    assert submissions == []

    original.write_text("version two\n", encoding="utf-8")
    edited = sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)
    assert edited["operations"] == 1
    assert [operation["kind"] for operation in submissions.pop()] == ["put"]

    added_path = docs / "added.txt"
    added_path.write_text("new\n", encoding="utf-8")
    added = sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)
    assert added["operations"] == 1
    assert submissions.pop()[0]["path"] == "local-files/folder-1/added.txt"

    renamed = docs / "renamed.md"
    original.rename(renamed)
    moved = sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)
    assert moved["operations"] == 1
    move = submissions.pop()[0]
    assert move["kind"] == "move"
    assert move["path"] == "local-files/folder-1/renamed.md"
    assert move["expected_content_hash"] == move["source_version"]

    added_path.unlink()
    deleted = sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)
    assert deleted["operations"] == 1
    assert submissions.pop()[0]["kind"] == "delete"


def test_tracker_rename_uses_ctxfs_normalized_content_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    original = docs / "original.md"
    original.write_bytes(b"line one\r\nline two\r\n")
    folder = FolderConfig(
        name="Documents", path=str(docs), prefix="local-files/folder-1"
    )
    paths = TrackerPaths(state_file=tmp_path / "state.json")
    submissions: list[list[dict]] = []
    monkeypatch.setattr(
        "ctxd.tracker._submit_operations",
        lambda endpoint, **kwargs: submissions.append(kwargs["operations"]),
    )
    sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)
    submissions.clear()

    original.rename(docs / "renamed.md")
    sync_once(folders=[folder], endpoint="http://ctxfs.local", paths=paths)

    move = submissions[0][0]
    normalized_hash = "sha256:" + hashlib.sha256(b"line one\nline two\n").hexdigest()
    assert move["kind"] == "move"
    assert move["expected_content_hash"] == normalized_hash
    assert move["expected_content_hash"] != move["source_version"]


def test_tracker_sync_migrates_legacy_state_with_a_safe_put(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    tracked_path = docs / "README.md"
    tracked_path.write_text("hello ctxfs\n", encoding="utf-8")
    folder = FolderConfig(
        name="Documents", path=str(docs), prefix="local-files/folder-1"
    )
    stat = tracked_path.stat()
    source_key = f"{folder.prefix}:{stat.st_dev}:{stat.st_ino}"
    source_version = "sha256:" + hashlib.sha256(tracked_path.read_bytes()).hexdigest()
    state_file = tmp_path / "state.json"
    state_file.write_text(
        '{"local-files/folder-1":{"%s":"%s"}}\n' % (source_key, source_version)
    )
    submissions = []
    monkeypatch.setattr(
        "ctxd.tracker._submit_operations",
        lambda endpoint, **kwargs: submissions.extend(kwargs["operations"]),
    )

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result["operations"] == 1
    assert submissions[0]["kind"] == "put"
    assert '"path": "local-files/folder-1/README.md"' in state_file.read_text()


def test_tracker_sync_submits_delete_for_missing_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    folder = FolderConfig(
        name="Documents",
        path=str(docs),
        prefix="local-files/folder-1",
    )
    state_file = tmp_path / "state.json"
    state_file.write_text(
        "{\n"
        '  "local-files/folder-1": {\n'
        '    "local-files/folder-1:1:2": "sha256:old"\n'
        "  }\n"
        "}\n"
    )
    submissions = []

    def submit(endpoint, *, writer_id, writer_prefix, operations):
        del endpoint, writer_id, writer_prefix
        submissions.extend(operations)

    monkeypatch.setattr("ctxd.tracker._submit_operations", submit)

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result == {"folders": 1, "operations": 1}
    assert submissions == [
        {
            "kind": "delete",
            "source_key": "local-files/folder-1:1:2",
            "source_version": "sha256:old",
        }
    ]


def test_tracker_sync_preserves_state_when_folder_scan_is_incomplete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing = tmp_path / "Missing"
    folder = FolderConfig(
        name="Documents",
        path=str(missing),
        prefix="local-files/folder-1",
    )
    state_file = tmp_path / "state.json"
    state_file.write_text(
        "{\n"
        '  "local-files/folder-1": {\n'
        '    "local-files/folder-1:1:2": "sha256:old"\n'
        "  }\n"
        "}\n"
    )
    submissions = []

    def submit(endpoint, *, writer_id, writer_prefix, operations):
        del endpoint, writer_id, writer_prefix
        submissions.extend(operations)

    monkeypatch.setattr("ctxd.tracker._submit_operations", submit)

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result == {"folders": 1, "operations": 0}
    assert submissions == []
    assert "local-files/folder-1:1:2" in state_file.read_text()


def test_tracker_sync_preserves_prior_file_after_read_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    source = docs / "notes.md"
    source.write_text("new content")
    folder = FolderConfig(
        name="Documents", path=str(docs), prefix="local-files/folder-1"
    )
    source_key = f"{folder.prefix}:{source.stat().st_dev}:{source.stat().st_ino}"
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({folder.prefix: {source_key: "sha256:old"}}) + "\n"
    )
    submissions = []
    original_read_bytes = Path.read_bytes

    def fail_target_read(path: Path) -> bytes:
        if path == source:
            raise OSError("simulated read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_target_read)
    monkeypatch.setattr(
        "ctxd.tracker._submit_operations",
        lambda endpoint, *, writer_id, writer_prefix, operations: submissions.extend(
            operations
        ),
    )

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result == {"folders": 1, "operations": 0}
    assert submissions == []
    assert source_key in state_file.read_text()


def test_tracker_sync_preserves_prior_file_after_stat_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    source = docs / "notes.md"
    source.write_text("new content")
    folder = FolderConfig(
        name="Documents", path=str(docs), prefix="local-files/folder-1"
    )
    initial_stat = source.stat()
    source_key = f"{folder.prefix}:{initial_stat.st_dev}:{initial_stat.st_ino}"
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({folder.prefix: {source_key: "sha256:old"}}) + "\n"
    )
    submissions = []
    original_stat = Path.stat

    def fail_target_stat(path: Path, *args, **kwargs):
        if path == source:
            raise OSError("simulated stat failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_target_stat)
    monkeypatch.setattr(
        "ctxd.tracker._submit_operations",
        lambda endpoint, *, writer_id, writer_prefix, operations: submissions.extend(
            operations
        ),
    )

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result == {"folders": 1, "operations": 0}
    assert submissions == []
    assert source_key in state_file.read_text()


def test_tracker_sync_preserves_prior_file_after_invalid_utf8(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    source = docs / "notes.md"
    source.write_bytes(b"\xff\xfe")
    folder = FolderConfig(
        name="Documents", path=str(docs), prefix="local-files/folder-1"
    )
    source_key = f"{folder.prefix}:{source.stat().st_dev}:{source.stat().st_ino}"
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({folder.prefix: {source_key: "sha256:old"}}) + "\n"
    )
    submissions = []
    monkeypatch.setattr(
        "ctxd.tracker._submit_operations",
        lambda endpoint, *, writer_id, writer_prefix, operations: submissions.extend(
            operations
        ),
    )

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result == {"folders": 1, "operations": 0}
    assert submissions == []
    assert source_key in state_file.read_text()


def test_incomplete_scan_updates_readable_file_without_deleting_unseen_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docs = tmp_path / "Docs"
    docs.mkdir()
    unreadable = docs / "unreadable.md"
    unreadable.write_bytes(b"\xff")
    readable = docs / "readable.md"
    readable.write_text("new readable content")
    folder = FolderConfig(
        name="Documents", path=str(docs), prefix="local-files/folder-1"
    )
    unreadable_key = (
        f"{folder.prefix}:{unreadable.stat().st_dev}:{unreadable.stat().st_ino}"
    )
    readable_key = (
        f"{folder.prefix}:{readable.stat().st_dev}:{readable.stat().st_ino}"
    )
    unseen_key = f"{folder.prefix}:1:999"
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                folder.prefix: {
                    unreadable_key: "sha256:old-unreadable",
                    readable_key: "sha256:old-readable",
                    unseen_key: "sha256:old-unseen",
                }
            }
        )
        + "\n"
    )
    submissions = []

    def submit(endpoint, *, writer_id, writer_prefix, operations):
        del endpoint, writer_id, writer_prefix
        submissions.extend(operations)

    monkeypatch.setattr("ctxd.tracker._submit_operations", submit)

    result = sync_once(
        folders=[folder],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result == {"folders": 1, "operations": 1}
    assert [operation["kind"] for operation in submissions] == ["put"]
    assert submissions[0]["source_key"] == readable_key
    saved = json.loads(state_file.read_text())[folder.prefix]
    assert unreadable_key in saved
    assert unseen_key in saved
    assert saved[readable_key]["source_version"] != "sha256:old-readable"


def test_tracker_sync_deletes_files_for_removed_configured_folder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        "{\n"
        '  "local-files/folder-1": {\n'
        '    "local-files/folder-1:1:2": "sha256:old"\n'
        "  }\n"
        "}\n"
    )
    submissions = []

    def submit(endpoint, *, writer_id, writer_prefix, operations):
        submissions.append(
            {
                "endpoint": endpoint,
                "writer_id": writer_id,
                "writer_prefix": writer_prefix,
                "operations": operations,
            }
        )

    monkeypatch.setattr("ctxd.tracker._submit_operations", submit)

    result = sync_once(
        folders=[],
        endpoint="http://ctxfs.local",
        paths=TrackerPaths(state_file=state_file),
    )

    assert result == {"folders": 0, "operations": 1}
    assert submissions == [
        {
            "endpoint": "http://ctxfs.local",
            "writer_id": "ctxd-tracker:local-files/folder-1",
            "writer_prefix": "local-files/folder-1",
            "operations": [
                {
                    "kind": "delete",
                    "source_key": "local-files/folder-1:1:2",
                    "source_version": "sha256:old",
                }
            ],
        }
    ]
    assert state_file.read_text() == "{}\n"


def test_tracker_run_forever_continues_after_sync_failure(monkeypatch) -> None:
    calls = {"sync": 0}

    def sync_once_side_effect():
        calls["sync"] += 1
        if calls["sync"] == 1:
            raise RuntimeError("temporary failure")

    def sleep_side_effect(interval):
        del interval
        if calls["sync"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("ctxd.tracker.sync_once", sync_once_side_effect)
    monkeypatch.setattr("ctxd.tracker.time.sleep", sleep_side_effect)

    with pytest.raises(KeyboardInterrupt):
        run_forever(interval_seconds=0.01)

    assert calls["sync"] == 2
