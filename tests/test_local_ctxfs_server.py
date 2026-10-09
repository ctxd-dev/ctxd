from __future__ import annotations

from fastapi.testclient import TestClient

from ctxd.local_ctxfs_server import create_ctxfs_app


def test_local_ctxfs_server_accepts_submission_and_reads_content(tmp_path) -> None:
    client = TestClient(create_ctxfs_app(tmp_path / "ctxfs"))

    response = client.post(
        "/api/ctxfs/submissions",
        json={
            "user_id": "existing-bridge-user",
            "writer_id": "writer-1",
            "writer_prefix": "local-files/root",
            "submission": {
                "run_id": "run-1",
                "sequence": 1,
                "is_full_scan": True,
                "operations": [
                    {
                        "kind": "put",
                        "path": "local-files/root/notes.md",
                        "source_key": "file-1",
                        "source_version": "v1",
                        "doc_class": "editable",
                        "file_format": "text/markdown",
                        "text": "hello ctxfs\n",
                        "provenance": {
                            "producer": "test",
                            "producer_version": "1",
                        },
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    read_response = client.get(
        "/api/ctxfs/read",
        params={"path": "local-files/root/notes.md"},
    )

    assert read_response.status_code == 200
    assert read_response.json()["text"] == "hello ctxfs\n"


def test_local_ctxfs_server_rejects_user_id_query(tmp_path) -> None:
    client = TestClient(create_ctxfs_app(tmp_path / "ctxfs"))

    response = client.get(
        "/api/ctxfs/tree",
        params={"user_id": "other"},
    )

    assert response.status_code == 400


def test_local_ctxfs_server_rejects_malformed_submission_operation(tmp_path) -> None:
    client = TestClient(create_ctxfs_app(tmp_path / "ctxfs"))

    response = client.post(
        "/api/ctxfs/submissions",
        json={
            "writer_id": "writer-1",
            "writer_prefix": "local-files/root",
            "submission": {
                "run_id": "run-1",
                "sequence": 1,
                "is_full_scan": True,
                "operations": [{"kind": "unknown"}],
            },
        },
    )

    assert response.status_code == 400


def test_local_ctxfs_server_rejects_overlapping_writer_prefix(tmp_path) -> None:
    client = TestClient(create_ctxfs_app(tmp_path / "ctxfs"))
    first = client.post(
        "/api/ctxfs/submissions",
        json={
            "writer_id": "writer-1",
            "writer_prefix": "local-files/root",
            "submission": {
                "run_id": "run-1",
                "sequence": 1,
                "is_full_scan": True,
                "operations": [],
            },
        },
    )

    second = client.post(
        "/api/ctxfs/submissions",
        json={
            "writer_id": "writer-2",
            "writer_prefix": "local-files/root/nested",
            "submission": {
                "run_id": "run-2",
                "sequence": 1,
                "is_full_scan": True,
                "operations": [],
            },
        },
    )

    assert first.status_code == 200
    assert second.status_code == 403
