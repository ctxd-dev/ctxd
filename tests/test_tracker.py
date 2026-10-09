from pathlib import Path

from ctxd.folders import FolderConfig
from ctxd.tracker import TrackerPaths, sync_once


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
    operation = submissions[0]["operations"][0]
    assert operation["kind"] == "put"
    assert operation["path"] == "local-files/folder-1/README.md"
    assert operation["text"] == "hello ctxfs\n"
    assert operation["file_format"] == "md"


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
        '{\n'
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
