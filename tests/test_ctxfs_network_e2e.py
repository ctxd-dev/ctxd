from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from ctxfs.credentials import CredentialStore


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def _server(root: Path, port: int):
    server_home = root.parent / f"{root.name}-home"
    server_home.mkdir(mode=0o700, exist_ok=True)
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("CTXD_"):
            environment.pop(name)
    environment["HOME"] = str(server_home)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ctxd.local_ctxfs_service",
            "serve",
            "--root",
            str(root),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"ctxfs exited during startup: {stdout}\n{stderr}")
            try:
                readiness = httpx.get(
                    f"http://127.0.0.1:{port}/health", timeout=0.2
                )
                if (
                    readiness.status_code == 401
                    and readiness.headers.get("www-authenticate") == "Bearer"
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("ctxfs did not begin listening")
        yield f"http://127.0.0.1:{port}"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _client_environment(home: Path, endpoint: str, token: str) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("CTXD_"):
            environment.pop(name)
    environment.update(
        {
            "HOME": str(home),
            "CTXD_BACKEND": "ctxfs",
            "CTXD_CTXFS_URL": endpoint,
            "CTXD_CTXFS_API_KEY": token,
            "CTXD_CONFIG_PATH": str(home / ".ctxd" / "config.json"),
            "CTXD_CREDENTIALS_PATH": str(home / ".ctxd" / "credentials.json"),
        }
    )
    return environment


def _run_module(
    module: str, *arguments: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", module, *arguments]
    if module == "ctxd.cli":
        # ctxd.cli is the console-script target and deliberately has no
        # ``python -m`` entry point. Exercise the exact same main function in a
        # fresh interpreter so this remains an end-to-end CLI boundary.
        command = [
            sys.executable,
            "-c",
            "from ctxd.cli import main; raise SystemExit(main())",
            *arguments,
        ]
    return subprocess.run(
        command,
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _submission(prefix: str, *, run_id: str = "run-1") -> dict[str, object]:
    return {
        "writer_id": "untrusted-client-value",
        "writer_prefix": prefix,
        "submission": {
            "run_id": run_id,
            "sequence": 1,
            "is_full_scan": True,
            "operations": [
                {
                    "kind": "put",
                    "path": f"{prefix}/manual.md",
                    "source_key": "manual-source",
                    "source_version": "v1",
                    "doc_class": "editable",
                    "file_format": "md",
                    "text": "network context\n",
                    "provenance": {"producer": "e2e", "producer_version": "1"},
                }
            ],
        },
    }


def test_isolated_tracker_server_and_cli_network_topology(tmp_path: Path) -> None:
    """No client receives the server root or another client's HOME."""
    server_root = tmp_path / "server-only"
    writer_home = tmp_path / "writer-only"
    reader_home = tmp_path / "reader-only"
    tracked = writer_home / "documents"
    tracked.mkdir(parents=True)
    (tracked / "notes.md").write_text("hello from the remote tracker\n")

    with CredentialStore(server_root) as credentials:
        writer = credentials.create(
            name="writer", scopes=["submit"], prefixes=["Writer"]
        )
        reader = credentials.create(name="reader", scopes=["read"])

    port = _free_port()
    with _server(server_root, port) as endpoint:
        # A server with no tracker and no content is a valid starting state.
        empty = httpx.get(
            f"{endpoint}/api/ctxfs/tree", headers=_auth(reader.token), timeout=2
        )
        assert empty.status_code == 200
        assert empty.json()["items"] == []

        writer_config = writer_home / ".ctxd" / "config.json"
        writer_config.parent.mkdir(parents=True)
        writer_config.write_text(
            json.dumps(
                {
                    "backend": "ctxfs",
                    "ctxfs_url": endpoint,
                    "folders": {
                        "Documents": {
                            "name": "Documents",
                            "path": str(tracked),
                            "prefix": "Writer",
                        }
                    },
                }
            )
        )
        writer_result = _run_module(
            "ctxd.tracker_service",
            "sync-once",
            environment=_client_environment(writer_home, endpoint, writer.token),
        )
        assert writer_result.returncode == 0, writer_result.stderr

        read = _run_module(
            "ctxd.cli",
            "files",
            "read",
            "Writer/notes.md",
            environment=_client_environment(reader_home, endpoint, reader.token),
        )
        assert read.returncode == 0, read.stderr
        assert read.stdout == "hello from the remote tracker\n"
        assert not (reader_home / ".ctxd" / "config.json").exists()

        # The same read scope protects HTTP MCP tools.
        mcp = httpx.post(
            f"{endpoint}/mcp",
            headers=_auth(reader.token),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "read",
                    "arguments": {"path": "Writer/notes.md"},
                },
            },
            timeout=2,
        )
        assert mcp.status_code == 200
        rest = httpx.get(
            f"{endpoint}/api/ctxfs/read",
            params={"path": "Writer/notes.md"},
            headers=_auth(reader.token),
            timeout=2,
        )
        assert rest.status_code == 200
        assert (
            mcp.json()["result"]["structuredContent"]["text"]
            == rest.json()["text"]
            == "hello from the remote tracker\n"
        )

        assert httpx.get(
            f"{endpoint}/api/ctxfs/tree", headers=_auth(writer.token), timeout=2
        ).status_code == 403
        assert httpx.post(
            f"{endpoint}/api/ctxfs/submissions",
            headers=_auth(reader.token),
            json=_submission("Writer"),
            timeout=2,
        ).status_code == 403
        assert httpx.post(
            f"{endpoint}/api/ctxfs/submissions",
            headers=_auth(writer.token),
            json=_submission("Other"),
            timeout=2,
        ).status_code == 403


def test_network_credentials_rotation_restart_and_abuse(tmp_path: Path) -> None:
    root = tmp_path / "persistent-server"
    with CredentialStore(root) as credentials:
        writer = credentials.create(
            name="writer", scopes=["submit"], prefixes=["Writer"]
        )
        reader = credentials.create(name="reader", scopes=["read"])
        status = credentials.create(name="status", scopes=["status"])
        expired = credentials.create(
            name="expired", scopes=["read"], expires_at=time.time() + 60
        )
        credentials.connection.execute(
            "UPDATE credentials SET expires_at = 0 WHERE key_id = ?",
            (expired.credential.key_id,),
        )
        credentials.connection.commit()
        revoked = credentials.create(name="revoked", scopes=["read"])
        credentials.revoke(revoked.credential.key_id)

    port = _free_port()
    with _server(root, port) as endpoint:
        health = httpx.get(
            f"{endpoint}/health", headers=_auth(status.token), timeout=2
        )
        assert health.status_code == 200
        assert health.json()["ok"] is True
        for authorization in (
            None,
            "Bearer",
            "Basic invalid",
            "Bearer wrong",
            f"Bearer {expired.token}",
            f"Bearer {revoked.token}",
        ):
            headers = {} if authorization is None else {"Authorization": authorization}
            response = httpx.get(f"{endpoint}/api/ctxfs/tree", headers=headers, timeout=2)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"

        first = httpx.post(
            f"{endpoint}/api/ctxfs/submissions",
            headers=_auth(writer.token),
            json=_submission("Writer"),
            timeout=2,
        )
        duplicate = httpx.post(
            f"{endpoint}/api/ctxfs/submissions",
            headers=_auth(writer.token),
            json=_submission("Writer"),
            timeout=2,
        )
        assert first.status_code == duplicate.status_code == 200

        with CredentialStore(root) as credentials:
            replacement = credentials.rotate(reader.credential.key_id)
        assert httpx.get(
            f"{endpoint}/api/ctxfs/tree", headers=_auth(reader.token), timeout=2
        ).status_code == 401
        tree = httpx.get(
            f"{endpoint}/api/ctxfs/tree", headers=_auth(replacement.token), timeout=2
        )
        assert tree.status_code == 200
        assert [item["path"] for item in tree.json()["items"]].count(
            "Writer/manual.md"
        ) == 1
        assert httpx.post(
            f"{endpoint}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            timeout=2,
        ).status_code == 401

    # Store and credential state survive a clean server recovery.
    with _server(root, port) as endpoint:
        assert httpx.get(
            f"{endpoint}/health", headers=_auth(status.token), timeout=2
        ).status_code == 200
        recovered = httpx.get(
            f"{endpoint}/api/ctxfs/read",
            params={"path": "Writer/manual.md"},
            headers=_auth(replacement.token),
            timeout=2,
        )
        assert recovered.status_code == 200
        assert recovered.json()["text"] == "network context\n"
        assert httpx.post(
            f"{endpoint}/mcp",
            headers=_auth(writer.token),
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            timeout=2,
        ).status_code == 403


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1"])
def test_non_loopback_plaintext_is_rejected_before_listening(
    tmp_path: Path, host: str
) -> None:
    result = _run_module(
        "ctxd.local_ctxfs_service",
        "serve",
        "--root",
        str(tmp_path / "server"),
        "--host",
        host,
        "--port",
        str(_free_port()),
        "--allow-network",
        environment=os.environ.copy(),
    )
    assert result.returncode != 0
    assert "requires --tls-cert and --tls-key" in result.stderr
