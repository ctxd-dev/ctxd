from __future__ import annotations

from fastapi.testclient import TestClient

from ctxd.local_ctxfs_server import CtxfsAuthMode, create_ctxfs_app
from ctxfs import CtxfsStore
from ctxfs.credentials import CredentialStore


def _bearer_client(tmp_path, *, scopes, prefixes=()):
    root = tmp_path / "ctxfs"
    with CredentialStore(root) as store:
        issued = store.create(name="test", scopes=scopes, prefixes=prefixes)
    client = TestClient(create_ctxfs_app(root, auth_mode=CtxfsAuthMode.BEARER))
    return client, issued


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _submission(prefix: str, path: str | None = None) -> dict:
    operations = []
    if path is not None:
        operations.append(
            {
                "kind": "put",
                "path": path,
                "source_key": f"source:{path}",
                "source_version": "v1",
                "doc_class": "editable",
                "file_format": "text/plain",
                "text": "secret",
                "provenance": {"producer": "test", "producer_version": "1"},
            }
        )
    return {
        "writer_id": "caller-controlled-and-ignored",
        "writer_prefix": prefix,
        "submission": {
            "run_id": "run-1",
            "sequence": 1,
            "is_full_scan": True,
            "operations": operations,
        },
    }


def test_local_ctxfs_server_accepts_submission_and_reads_content(tmp_path) -> None:
    client = TestClient(
        create_ctxfs_app(tmp_path / "ctxfs", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL)
    )

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
    client = TestClient(
        create_ctxfs_app(tmp_path / "ctxfs", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL)
    )

    response = client.get(
        "/api/ctxfs/tree",
        params={"user_id": "other"},
    )

    assert response.status_code == 400


def test_local_ctxfs_server_returns_404_for_missing_stat_path(tmp_path) -> None:
    client = TestClient(
        create_ctxfs_app(tmp_path / "ctxfs", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL)
    )

    response = client.get(
        "/api/ctxfs/stat",
        params={"path": "local-files/root/missing.md"},
    )

    assert response.status_code == 404


def test_local_ctxfs_server_rejects_malformed_submission_operation(tmp_path) -> None:
    client = TestClient(
        create_ctxfs_app(tmp_path / "ctxfs", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL)
    )

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
    client = TestClient(
        create_ctxfs_app(tmp_path / "ctxfs", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL)
    )
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


def test_local_ctxfs_server_exposes_mcp_tool_discovery(tmp_path) -> None:
    client = TestClient(
        create_ctxfs_app(tmp_path / "ctxfs", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL)
    )

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "ls",
        "tree",
        "glob",
        "stat",
        "read",
        "read_lines",
        "grep",
    ]


def test_local_ctxfs_mcp_reads_in_process(tmp_path) -> None:
    client = TestClient(
        create_ctxfs_app(tmp_path / "ctxfs", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL)
    )
    submission = client.post(
        "/api/ctxfs/submissions",
        json={
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
    assert submission.status_code == 200

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {"path": "local-files/root/notes.md"},
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["text"] == "hello ctxfs\n"
    assert result.get("isError") is None


def test_bearer_mode_rejects_missing_malformed_invalid_and_revoked_keys(
    tmp_path,
) -> None:
    client, issued = _bearer_client(tmp_path, scopes=["read"])

    for headers in (
        {},
        {"Authorization": issued.token},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer invalid"},
    ):
        response = client.get("/api/ctxfs/tree", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    with CredentialStore(tmp_path / "ctxfs") as store:
        store.revoke(issued.credential.key_id)
    assert client.get("/api/ctxfs/tree", headers=_auth(issued.token)).status_code == 401


def test_bearer_authentication_is_fail_closed_before_routing_and_validation(
    tmp_path,
) -> None:
    client, issued = _bearer_client(tmp_path, scopes=["read"])

    for path in ("/openapi.json", "/docs", "/not-a-route"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    malformed = client.post(
        "/mcp",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 401
    assert malformed.headers["www-authenticate"] == "Bearer"

    assert client.get("/not-a-route", headers=_auth(issued.token)).status_code == 404


def test_bearer_mode_rejects_expired_key(tmp_path) -> None:
    client, issued = _bearer_client(tmp_path, scopes=["read"])
    with CredentialStore(tmp_path / "ctxfs") as store, store.connection:
        store.connection.execute(
            "UPDATE credentials SET expires_at = 0 WHERE key_id = ?",
            (issued.credential.key_id,),
        )
    assert client.get("/api/ctxfs/tree", headers=_auth(issued.token)).status_code == 401


def test_rest_scopes_are_enforced_without_query_or_body_bypass(tmp_path) -> None:
    reader, read_key = _bearer_client(tmp_path, scopes=["read"])
    assert (
        reader.get("/api/ctxfs/tree", headers=_auth(read_key.token)).status_code == 200
    )
    assert reader.get("/health", headers=_auth(read_key.token)).status_code == 403
    assert (
        reader.post(
            "/api/ctxfs/submissions?user_id=local",
            headers=_auth(read_key.token),
            json=_submission("Allowed"),
        ).status_code
        == 403
    )

    status_client, status_key = _bearer_client(tmp_path / "status", scopes=["status"])
    assert (
        status_client.get("/health", headers=_auth(status_key.token)).status_code == 200
    )
    assert (
        status_client.get(
            "/api/ctxfs/tree?user_id=local", headers=_auth(status_key.token)
        ).status_code
        == 403
    )


def test_submission_authority_comes_from_key_and_granted_prefix(tmp_path) -> None:
    client, issued = _bearer_client(tmp_path, scopes=["submit"], prefixes=["Allowed"])
    headers = _auth(issued.token)

    accepted = client.post(
        "/api/ctxfs/submissions",
        headers=headers,
        json={**_submission("Allowed", "Allowed/note.txt"), "user_id": "admin"},
    )
    assert accepted.status_code == 200

    wrong_prefix = client.post(
        "/api/ctxfs/submissions",
        headers=headers,
        json=_submission("Forbidden", "Forbidden/note.txt"),
    )
    assert wrong_prefix.status_code == 403
    assert wrong_prefix.json()["detail"] == "prefix_not_granted"

    path_escape = client.post(
        "/api/ctxfs/submissions",
        headers=headers,
        json=_submission("Allowed", "AllowedElsewhere/note.txt"),
    )
    assert path_escape.status_code == 403


def test_submission_writer_uses_server_grant_not_claimed_subprefix(tmp_path) -> None:
    client, issued = _bearer_client(tmp_path, scopes=["submit"], prefixes=["Allowed"])

    accepted = client.post(
        "/api/ctxfs/submissions",
        headers=_auth(issued.token),
        json=_submission("Allowed/subdir", "Allowed/subdir/note.txt"),
    )
    assert accepted.status_code == 200

    # The registered writer and ownership root come from the credential grant,
    # rather than allowing request metadata to mint arbitrary writer roots.
    store = CtxfsStore(tmp_path / "ctxfs")
    try:
        rows = store.connection.execute(
            "SELECT writer_id, prefix FROM writer_prefixes"
        ).fetchall()
    finally:
        store.close()
    assert [(row["writer_id"], row["prefix"]) for row in rows] == [
        (f"{issued.credential.key_id}:Allowed", "Allowed")
    ]


def test_mcp_and_rest_have_matching_authentication_and_scope_denials(tmp_path) -> None:
    client, status_key = _bearer_client(tmp_path, scopes=["status"])
    list_message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    assert client.get("/api/ctxfs/tree").status_code == 401
    assert client.post("/mcp", json=list_message).status_code == 401
    assert (
        client.get("/api/ctxfs/tree", headers=_auth(status_key.token)).status_code
        == 403
    )
    assert (
        client.post(
            "/mcp", headers=_auth(status_key.token), json=list_message
        ).status_code
        == 403
    )

    initialize = {"jsonrpc": "2.0", "id": 2, "method": "initialize"}
    assert (
        client.post(
            "/mcp", headers=_auth(status_key.token), json=initialize
        ).status_code
        == 200
    )
    initialized = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    assert (
        client.post(
            "/mcp", headers=_auth(status_key.token), json=initialized
        ).status_code
        == 202
    )

    with CredentialStore(tmp_path / "ctxfs") as store:
        read_key = store.create(name="reader", scopes=["read"])
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "tree", "arguments": {}},
    }
    assert (
        client.post(
            "/mcp", headers=_auth(read_key.token), json=list_message
        ).status_code
        == 200
    )
    assert (
        client.post("/mcp", headers=_auth(read_key.token), json=call).status_code == 200
    )


def test_machine_enrollment_rotation_and_revocation_are_bound_to_catalog(tmp_path):
    root = tmp_path / "ctxfs"
    with CredentialStore(root) as credentials:
        enrollment = credentials.create(name="enroll", scopes=["enroll"])
        reader = credentials.create(name="reader", scopes=["read"])
    client = TestClient(create_ctxfs_app(root, auth_mode=CtxfsAuthMode.BEARER))
    registration_body = {
        "local_registration_id": "local-machine-1",
        "name": "Alice's Mac",
        "description": "Research",
    }

    registered = client.post(
        "/api/ctxfs/machines/register",
        headers=_auth(enrollment.token),
        json=registration_body,
    )
    assert registered.status_code == 201
    first = registered.json()
    machine_id = first["machine"]["machine_id"]
    prefix = f"machines/{first['machine']['machine_key']}"
    assert set(first) == {"machine", "key_id", "token"}

    assert (
        client.post(
            "/api/ctxfs/machines/register",
            headers=_auth(enrollment.token),
            json={**registration_body, "name": "Other"},
        ).status_code
        == 409
    )
    recovered = client.post(
        "/api/ctxfs/machines/register",
        headers=_auth(enrollment.token),
        json=registration_body,
    )
    assert recovered.status_code == 200
    current = recovered.json()
    assert current["key_id"] != first["key_id"]
    assert client.get("/not-a-route", headers=_auth(first["token"])).status_code == 401

    current_headers = _auth(current["token"])
    wrong_machine = client.post(
        "/api/ctxfs/machines/not-this-machine/credentials/prepare",
        headers=current_headers,
    )
    assert wrong_machine.status_code == 403
    accepted = client.post(
        "/api/ctxfs/submissions",
        headers=current_headers,
        json=_submission(prefix),
    )
    assert accepted.status_code == 200

    prepared_response = client.post(
        f"/api/ctxfs/machines/{machine_id}/credentials/prepare",
        headers=current_headers,
    )
    assert prepared_response.status_code == 200
    prepared = prepared_response.json()
    assert (
        client.post(
            "/api/ctxfs/submissions",
            headers=_auth(prepared["token"]),
            json=_submission(prefix),
        ).status_code
        == 403
    )
    confirmed = client.post(
        f"/api/ctxfs/machines/{machine_id}/credentials/confirm",
        headers=current_headers,
        json={"replacement_key_id": prepared["key_id"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["credential_key_id"] == prepared["key_id"]
    assert client.get("/not-a-route", headers=current_headers).status_code == 401

    replacement_headers = _auth(prepared["token"])
    revoked = client.delete(
        f"/api/ctxfs/machines/{machine_id}/credential",
        headers=replacement_headers,
    )
    assert revoked.status_code == 204
    assert client.get("/not-a-route", headers=replacement_headers).status_code == 401
    assert client.get("/api/ctxfs/tree", headers=_auth(reader.token)).status_code == 200


def test_admin_can_enroll_without_consuming_single_use_credential(tmp_path):
    root = tmp_path / "ctxfs"
    with CredentialStore(root) as credentials:
        admin = credentials.create(name="admin", scopes=["admin"])
    client = TestClient(create_ctxfs_app(root, auth_mode=CtxfsAuthMode.BEARER))

    for index in range(2):
        response = client.post(
            "/api/ctxfs/machines/register",
            headers=_auth(admin.token),
            json={
                "local_registration_id": f"machine-{index}",
                "name": f"Machine {index}",
            },
        )
        assert response.status_code == 201


def test_authenticated_registration_rejects_invalid_catalog_text_at_boundary(tmp_path):
    root = tmp_path / "ctxfs"
    with CredentialStore(root) as credentials:
        admin = credentials.create(name="admin", scopes=["admin"])
    client = TestClient(create_ctxfs_app(root, auth_mode=CtxfsAuthMode.BEARER))

    for field, value in (
        ("name", " \t "),
        ("name", "bad\nname"),
        ("platform", "   "),
        ("platform", "bad\tplatform"),
        ("ctxd_version", " \n"),
        ("ctxd_version", "bad\x7fversion"),
    ):
        body = {"local_registration_id": "machine-1", "name": "Laptop"}
        body[field] = value
        response = client.post(
            "/api/ctxfs/machines/register",
            headers=_auth(admin.token),
            json=body,
        )
        assert response.status_code == 422
        assert len(response.content) < 2048
