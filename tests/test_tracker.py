import hashlib
import json
from pathlib import Path

import pytest

from ctxd.folders import FolderConfig
from ctxd.tracker import (
    SAFE_SYNC_ERROR_MESSAGE,
    TrackerPaths,
    _submit_operations,
    load_tracker_health,
    run_forever,
    sync_once,
)


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

    assert result == {"folders": 1, "operations": 1, "complete": True}
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

    assert result == {"folders": 1, "operations": 1, "complete": True}
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

    assert result == {"folders": 1, "operations": 0, "complete": False}
    assert submissions == []
    assert "local-files/folder-1:1:2" in state_file.read_text()


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

    assert result == {"folders": 0, "operations": 1, "complete": True}
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


def test_tracker_run_forever_continues_after_sync_failure(
    tmp_path: Path, monkeypatch
) -> None:
    calls = {"sync": 0}

    def sync_once_side_effect(**kwargs):
        del kwargs
        calls["sync"] += 1
        if calls["sync"] == 1:
            raise RuntimeError("temporary failure")
        return {"folders": 0, "operations": 0, "complete": True}

    def sleep_side_effect(interval):
        del interval
        if calls["sync"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("ctxd.tracker.sync_once", sync_once_side_effect)
    monkeypatch.setattr("ctxd.tracker.time.sleep", sleep_side_effect)

    with pytest.raises(KeyboardInterrupt):
        run_forever(
            interval_seconds=0.01,
            paths=TrackerPaths(
                state_file=tmp_path / "state.json",
                health_file=tmp_path / "health.json",
            ),
        )

    assert calls["sync"] == 2


def test_tracker_health_records_success_and_preserves_it_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    paths = TrackerPaths(
        state_file=tmp_path / "local" / "tracker-state.json",
        health_file=tmp_path / "local" / "tracker-health.json",
    )
    results: list[object] = [
        {"folders": 2, "operations": 3, "complete": True},
        RuntimeError("secret /Users/alice https://ctxfs.example token-123"),
    ]

    def sync_side_effect(**kwargs):
        assert kwargs == {"paths": paths}
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    sleeps = 0

    def sleep_side_effect(interval):
        nonlocal sleeps
        assert interval == 0.01
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    ticks = iter([100, 200])
    monkeypatch.setattr("ctxd.tracker.sync_once", sync_side_effect)
    monkeypatch.setattr("ctxd.tracker.time.sleep", sleep_side_effect)

    with pytest.raises(KeyboardInterrupt):
        run_forever(interval_seconds=0.01, paths=paths, clock=lambda: next(ticks))

    health = load_tracker_health(paths.health_file)
    assert health == {
        "version": 1,
        "last_attempt": 200,
        "last_success": 100,
        "last_result": "failed",
        "last_error_category": "RuntimeError",
        "last_error_message": SAFE_SYNC_ERROR_MESSAGE,
        "complete": None,
        "folders_scanned": None,
        "operations": None,
    }
    persisted = paths.health_file.read_text()
    assert "/Users/alice" not in persisted
    assert "ctxfs.example" not in persisted
    assert "token-123" not in persisted
    assert paths.health_file.stat().st_mode & 0o777 == 0o600
    assert paths.health_file.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "health",
    [
        {
            "version": 1,
            "last_attempt": 10,
            "last_success": 10,
            "last_result": "success",
            "last_error_category": None,
            "last_error_message": None,
            "complete": True,
            "folders_scanned": 2,
            "operations": 3,
        },
        {
            "version": 1,
            "last_attempt": 20,
            "last_success": 10,
            "last_result": "incomplete",
            "last_error_category": None,
            "last_error_message": None,
            "complete": False,
            "folders_scanned": 2,
            "operations": 1,
        },
        {
            "version": 1,
            "last_attempt": 30,
            "last_success": 10,
            "last_result": "failed",
            "last_error_category": "RuntimeError",
            "last_error_message": SAFE_SYNC_ERROR_MESSAGE,
            "complete": None,
            "folders_scanned": None,
            "operations": None,
        },
    ],
)
def test_load_tracker_health_accepts_writer_record_shapes(
    tmp_path: Path, health: dict[str, object]
) -> None:
    health_file = tmp_path / "tracker-health.json"
    health_file.write_text(json.dumps(health))

    assert load_tracker_health(health_file) == health


def test_tracker_health_marks_incomplete_without_advancing_last_success(
    tmp_path: Path, monkeypatch
) -> None:
    paths = TrackerPaths(
        state_file=tmp_path / "tracker-state.json",
        health_file=tmp_path / "tracker-health.json",
    )
    calls = iter(
        [
            {"folders": 1, "operations": 4, "complete": True},
            {"folders": 1, "operations": 0, "complete": False},
        ]
    )
    monkeypatch.setattr("ctxd.tracker.sync_once", lambda **kwargs: next(calls))
    sleeps = 0

    def stop_after_two(_interval):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("ctxd.tracker.time.sleep", stop_after_two)
    ticks = iter([10, 20])

    with pytest.raises(KeyboardInterrupt):
        run_forever(paths=paths, clock=lambda: next(ticks))

    health = load_tracker_health(paths.health_file)
    assert health is not None
    assert health["last_result"] == "incomplete"
    assert health["last_attempt"] == 20
    assert health["last_success"] == 10
    assert health["complete"] is False
    assert health["folders_scanned"] == 1


def test_tracker_health_survives_restart_and_write_failures_do_not_stop_sync(
    tmp_path: Path, monkeypatch
) -> None:
    paths = TrackerPaths(
        state_file=tmp_path / "tracker-state.json",
        health_file=tmp_path / "tracker-health.json",
    )
    paths.health_file.write_text(
        '{"complete":true,"folders_scanned":1,"last_attempt":10,'
        '"last_error_category":null,"last_error_message":null,'
        '"last_result":"success","last_success":10,"operations":1,"version":1}\n'
    )
    calls = 0

    def successful_sync(**kwargs):
        nonlocal calls
        calls += 1
        return {"folders": 1, "operations": 0, "complete": True}

    monkeypatch.setattr("ctxd.tracker.sync_once", successful_sync)
    monkeypatch.setattr(
        "ctxd.tracker._save_health", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read only"))
    )
    monkeypatch.setattr(
        "ctxd.tracker.time.sleep", lambda _interval: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(KeyboardInterrupt):
        run_forever(paths=paths, clock=lambda: 20)

    assert calls == 1
