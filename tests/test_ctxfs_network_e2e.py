from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from ctxfs.credentials import CredentialStore


DEMO_DOCUMENTS = {
    "google_drive/handbook/onboarding.md": """# Engineering onboarding checklist

## Week one — hardware

Every engineer is issued a laptop from the standard build: 16-inch, 36 GB RAM, 1 TB disk.
Swaps outside the standard build need a written reason and go to the finance lead.
""",
    "google_drive/handbook/equipment-approval.md": """# Equipment approval policy

Standard-build requests are approved by the hiring manager and need no further sign-off.
Anything above the standard build goes to the finance lead, and needs a written reason.
""",
    "google_drive/handbook/time-off.md": """# Time off and holidays

Annual leave is 25 days plus public holidays. Book it with your manager.
Unpaid leave is arranged case by case with your manager.
This document does not cover sabbaticals.
""",
    "google_drive/retros/2026-03-12-checkout.md": """# Retro — checkout outage, 12 March

Customer impact was 41 minutes of failed checkout attempts.
A connection-pool ceiling and a health check that did not test the dependency caused it.
""",
    "github/it-requests/README.md": """# it-requests

Open an issue using the `hardware` template. IT picks it up once the `approval` label is set.
""",
    # ctxfs indexes files, so the demo's extensionless Slack UID is represented
    # as Markdown while preserving its source hierarchy and content.
    "slack/channels/incidents.md": """# #incidents

09:14 Paging on-call — checkout 5xx rate is climbing.
09:55 Recovered. Error rate back to baseline. Retro tomorrow.
""",
}


# Public read-operation E2E matrix:
#
# | operation  | managed CLI | authenticated REST | authenticated MCP |
# |------------|-------------|--------------------|-------------------|
# | stat       | yes         | yes                | yes               |
# | ls         | yes         | yes                | yes               |
# | tree       | yes         | yes                | yes               |
# | glob       | yes         | yes                | yes               |
# | read       | yes         | yes                | yes               |
# | read-lines | yes         | yes                | read_lines: yes   |
# | grep       | yes         | yes                | yes               |


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def _server(
    root: Path,
    port: int,
    *,
    tls_cert: Path | None = None,
    tls_key: Path | None = None,
    output: dict[str, str] | None = None,
):
    server_home = root.parent / f"{root.name}-home"
    server_home.mkdir(mode=0o700, exist_ok=True)
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("CTXD_"):
            environment.pop(name)
    environment["HOME"] = str(server_home)
    command = [
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
    ]
    if tls_cert is not None and tls_key is not None:
        command.extend(["--tls-cert", str(tls_cert), "--tls-key", str(tls_key)])
    process = subprocess.Popen(
        command,
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
                scheme = "https" if tls_cert is not None else "http"
                readiness = httpx.get(
                    f"{scheme}://127.0.0.1:{port}/health",
                    timeout=0.2,
                    verify=False,
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
        scheme = "https" if tls_cert is not None else "http"
        yield f"{scheme}://127.0.0.1:{port}"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stdout, stderr = process.communicate()
        if output is not None:
            output.update(stdout=stdout, stderr=stderr)


def _certificate(tmp_path: Path) -> tuple[Path, Path]:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.fail("OpenSSL is required for the HTTPS end-to-end test")
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    result = subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    private_key.chmod(0o600)
    return certificate, private_key


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


def _isolated_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("CTXD_"):
            environment.pop(name)
    environment.update(
        {
            "HOME": str(home),
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


def _cli_json(*arguments: str, environment: dict[str, str]) -> dict[str, object]:
    result = _run_module("ctxd.cli", *arguments, environment=environment)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _write_documents(root: Path, documents: dict[str, str]) -> None:
    for relative_path, text in documents.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _assert_cli_demo_state(
    environment: dict[str, str],
    prefix: str,
    documents: dict[str, str],
    *,
    grep_pattern: str,
) -> None:
    """Exercise every public read command against one coherent snapshot."""
    paths = {f"{prefix}/{path}" for path in documents}
    tree = _cli_json("files", "tree", "Documents", environment=environment)
    tree_files = {item["path"] for item in tree["items"] if item["kind"] == "file"}
    assert tree_files == paths

    listing = _cli_json(
        "files", "ls", "Documents/google_drive/handbook", environment=environment
    )
    handbook_paths = {
        f"{prefix}/{path}"
        for path in documents
        if path.startswith("google_drive/handbook/")
    }
    assert {item["path"] for item in listing["items"]} == handbook_paths

    markdown = _cli_json("files", "glob", "Documents/**/*.md", environment=environment)
    assert {item["path"] for item in markdown["items"]} == {
        path for path in paths if path.endswith(".md")
    }

    relative_path = "google_drive/handbook/onboarding.md"
    text = documents[relative_path]
    canonical_path = f"{prefix}/{relative_path}"
    stat = _cli_json(
        "files", "stat", f"Documents/{relative_path}", environment=environment
    )
    assert stat["path"] == canonical_path
    assert stat["size_bytes"] == len(text.encode())
    assert stat["line_count"] == len(text.splitlines())

    read = _run_module(
        "ctxd.cli",
        "files",
        "read",
        f"Documents/{relative_path}",
        environment=environment,
    )
    assert read.returncode == 0, read.stderr
    assert read.stdout == text

    lines = _cli_json(
        "files",
        "read-lines",
        f"Documents/{relative_path}",
        "--start",
        "1",
        "--end",
        "2",
        "--json",
        environment=environment,
    )
    assert lines["lines"] == text.splitlines()[:2]

    grep = _cli_json(
        "files", "grep", grep_pattern, "--prefix", "Documents", environment=environment
    )
    compiled_pattern = re.compile(grep_pattern)
    expected_grep_hits = []
    for path, body in documents.items():
        for line_number, line in enumerate(body.splitlines(), start=1):
            if match := compiled_pattern.search(line):
                expected_grep_hits.append(
                    (f"{prefix}/{path}", line_number, match.start())
                )
    actual_grep_hits = [
        (item["path"], item["line_number"], item["match_start"])
        for item in grep["items"]
    ]
    assert sorted(actual_grep_hits) == sorted(expected_grep_hits)


def _assert_rest_mcp_parity(
    endpoint: str,
    token: str,
    operations: list[tuple[str, str, dict[str, object], dict[str, object]]],
) -> dict[str, dict[str, object]]:
    results = {}
    for tool, route, query, arguments in operations:
        rest = httpx.get(
            f"{endpoint}{route}", params=query, headers=_auth(token), timeout=2
        )
        assert rest.status_code == 200, rest.text
        mcp = _mcp_request(
            endpoint,
            token,
            "tools/call",
            params={"name": tool, "arguments": arguments},
        )
        assert mcp["result"]["structuredContent"] == rest.json()
        results[tool] = rest.json()
    return results


def _mcp_request(
    endpoint: str,
    token: str,
    method: str,
    *,
    params: dict[str, object] | None = None,
    request_id: int = 1,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    response = httpx.post(
        f"{endpoint}/mcp",
        headers=_auth(token),
        json=payload,
        timeout=2,
    )
    assert response.status_code == 200, response.text
    return response.json()


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


def test_managed_ctxfs_full_cli_lifecycle(tmp_path: Path) -> None:
    # Keep the managed Unix socket below macOS's short AF_UNIX path limit.
    home = Path(tempfile.mkdtemp(prefix="ctxd-e2e-"))
    tracked = tmp_path / "source"
    tracked.mkdir()
    documents = dict(DEMO_DOCUMENTS)
    _write_documents(tracked, documents)
    (tracked / ".hidden.md").write_text("hidden\n", encoding="utf-8")
    (tracked / "binary.md").write_bytes(b"\xff\xfe")
    (tracked / "unsupported.bin").write_text("unsupported\n", encoding="utf-8")
    (tracked / "extensionless").write_text("extensionless\n", encoding="utf-8")
    (tracked / "oversized.txt").write_bytes(b"x" * 5_000_001)
    environment = _isolated_environment(home)

    try:
        initialized = _run_module(
            "ctxd.cli", "backend", "set", "ctxfs", environment=environment
        )
        assert initialized.returncode == 0, initialized.stderr
        assert "ctxfs managed locally: True" in initialized.stdout

        registered = _run_module(
            "ctxd.cli",
            "folders",
            "add",
            str(tracked),
            "--name",
            "Documents",
            environment=environment,
        )
        assert registered.returncode == 0, registered.stderr
        config = json.loads((home / ".ctxd" / "config.json").read_text())
        prefix = config["folders"]["Documents"]["prefix"]
        synchronized = _run_module(
            "ctxd.tracker_service", "sync-once", environment=environment
        )
        assert synchronized.returncode == 0, synchronized.stderr

        _assert_cli_demo_state(environment, prefix, documents, grep_pattern="checkout")
        tree = _cli_json("files", "tree", "Documents", environment=environment)
        tree_paths = {item["path"] for item in tree["items"]}
        assert not any(
            name in path
            for path in tree_paths
            for name in (
                ".hidden.md",
                "binary.md",
                "unsupported.bin",
                "extensionless",
                "oversized.txt",
            )
        )

        bounded = _cli_json(
            "files", "tree", "Documents", "--limit", "1", environment=environment
        )
        assert bounded["complete"] is False
        assert bounded["stopped_by"] == "limit"
        truncated = _cli_json(
            "files",
            "read",
            "Documents/google_drive/handbook/onboarding.md",
            "--max-bytes",
            "5",
            "--json",
            environment=environment,
        )
        assert truncated["text"] == "# Eng"
        assert truncated["complete"] is False
        assert truncated["stopped_by"] == "max_bytes"

        missing = _run_module(
            "ctxd.cli",
            "files",
            "stat",
            "Documents/missing.md",
            environment=environment,
        )
        assert missing.returncode != 0

        # Remove the unreadable eligible file so subsequent scans are complete
        # and may safely infer deletions.
        (tracked / "binary.md").unlink()
        onboarding = "google_drive/handbook/onboarding.md"
        documents[onboarding] = documents[onboarding].replace("36 GB", "64 GB")
        (tracked / onboarding).write_text(documents[onboarding], encoding="utf-8")
        added = "google_drive/handbook/security.md"
        documents[added] = (
            "# Security\n\nReport security issues to the security lead.\n"
        )
        _write_documents(tracked, {added: documents[added]})
        old_retro = "google_drive/retros/2026-03-12-checkout.md"
        new_retro = "google_drive/retros/checkout-outage.md"
        (tracked / old_retro).rename(tracked / new_retro)
        documents[new_retro] = documents.pop(old_retro)
        old_time_off = "google_drive/handbook/time-off.md"
        new_time_off = "google_drive/handbook/time-off.txt"
        (tracked / old_time_off).rename(tracked / new_time_off)
        documents[new_time_off] = documents.pop(old_time_off)
        changed = _run_module(
            "ctxd.tracker_service", "sync-once", environment=environment
        )
        assert changed.returncode == 0, changed.stderr
        _assert_cli_demo_state(environment, prefix, documents, grep_pattern="64 GB")
        for moved_path in (new_retro, new_time_off):
            moved_text = documents[moved_path]
            moved_stat = _cli_json(
                "files", "stat", f"Documents/{moved_path}", environment=environment
            )
            assert moved_stat["path"] == f"{prefix}/{moved_path}"
            assert moved_stat["size_bytes"] == len(moved_text.encode())
            moved_read = _run_module(
                "ctxd.cli",
                "files",
                "read",
                f"Documents/{moved_path}",
                environment=environment,
            )
            assert moved_read.returncode == 0, moved_read.stderr
            assert moved_read.stdout == moved_text
            moved_lines = _cli_json(
                "files",
                "read-lines",
                f"Documents/{moved_path}",
                "--start",
                "1",
                "--end",
                "2",
                "--json",
                environment=environment,
            )
            assert moved_lines["lines"] == moved_text.splitlines()[:2]
        moved_grep = _cli_json(
            "files",
            "grep",
            "connection-pool ceiling",
            "--prefix",
            "Documents",
            environment=environment,
        )
        assert {item["path"] for item in moved_grep["items"]} == {
            f"{prefix}/{new_retro}"
        }
        for stale in (old_retro, old_time_off):
            missing = _run_module(
                "ctxd.cli",
                "files",
                "stat",
                f"Documents/{stale}",
                environment=environment,
            )
            assert missing.returncode != 0
            stale_read = _run_module(
                "ctxd.cli",
                "files",
                "read",
                f"Documents/{stale}",
                environment=environment,
            )
            assert stale_read.returncode != 0
        stale_content = _cli_json(
            "files", "grep", "36 GB", "--prefix", "Documents", environment=environment
        )
        assert stale_content["items"] == []

        deleted_path = "google_drive/handbook/equipment-approval.md"
        (tracked / deleted_path).unlink()
        documents.pop(deleted_path)
        deleted = _run_module(
            "ctxd.tracker_service", "sync-once", environment=environment
        )
        assert deleted.returncode == 0, deleted.stderr
        _assert_cli_demo_state(environment, prefix, documents, grep_pattern="checkout")
        assert (
            _run_module(
                "ctxd.cli",
                "files",
                "read",
                f"Documents/{deleted_path}",
                environment=environment,
            ).returncode
            != 0
        )
        deleted_grep = _cli_json(
            "files",
            "grep",
            "Standard-build requests",
            "--prefix",
            "Documents",
            environment=environment,
        )
        assert deleted_grep["items"] == []
    finally:
        stopped = subprocess.run(
            [
                sys.executable,
                "-c",
                "from ctxd.local_ctxfs_service import stop; stop()",
            ],
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert stopped.returncode == 0, stopped.stderr
        assert not (home / ".ctxd" / "local" / "ctxfs.sock").exists()
        assert not (home / ".ctxd" / "local" / "ctxfs.pid").exists()
        shutil.rmtree(home)
        assert not home.exists()


def test_isolated_tracker_server_and_cli_network_topology(tmp_path: Path) -> None:
    """No client receives the server root or another client's HOME."""
    server_root = tmp_path / "server-only"
    writer_home = tmp_path / "writer-only"
    reader_home = tmp_path / "reader-only"
    tracked = writer_home / "documents"
    tracked.mkdir(parents=True)
    (tracked / "notes.md").write_text("hello from the remote tracker\n")
    (tracked / "report.txt").write_text("remote needle\nsecond line\n")

    with CredentialStore(server_root) as credentials:
        writer = credentials.create(
            name="writer", scopes=["submit"], prefixes=["Writer"]
        )
        reader = credentials.create(name="reader", scopes=["read", "status"])

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
        assert writer.token not in writer_result.stdout + writer_result.stderr

        read = _run_module(
            "ctxd.cli",
            "files",
            "read",
            "Writer/notes.md",
            environment=_client_environment(reader_home, endpoint, reader.token),
        )
        assert read.returncode == 0, read.stderr
        assert read.stdout == "hello from the remote tracker\n"
        assert reader.token not in read.stdout + read.stderr
        assert not (reader_home / ".ctxd" / "config.json").exists()

        health = httpx.get(f"{endpoint}/health", headers=_auth(reader.token), timeout=2)
        assert health.status_code == 200
        assert health.json()["ok"] is True

        initialized = _mcp_request(endpoint, reader.token, "initialize")
        assert initialized["result"]["protocolVersion"] == "2025-06-18"
        assert _mcp_request(endpoint, reader.token, "ping")["result"] == {}
        listed = _mcp_request(endpoint, reader.token, "tools/list")
        assert [tool["name"] for tool in listed["result"]["tools"]] == [
            "ls",
            "tree",
            "glob",
            "stat",
            "read",
            "read_lines",
            "grep",
        ]

        operations = [
            (
                "stat",
                "/api/ctxfs/stat",
                {"path": "Writer/notes.md"},
                {"path": "Writer/notes.md"},
            ),
            ("ls", "/api/ctxfs/ls", {"path": "Writer"}, {"path": "Writer"}),
            (
                "tree",
                "/api/ctxfs/tree",
                {"prefix": "Writer"},
                {"prefix": "Writer"},
            ),
            (
                "glob",
                "/api/ctxfs/glob",
                {"pattern": "Writer/*.md"},
                {"pattern": "Writer/*.md"},
            ),
            (
                "read",
                "/api/ctxfs/read",
                {"path": "Writer/notes.md"},
                {"path": "Writer/notes.md"},
            ),
            (
                "read_lines",
                "/api/ctxfs/read-lines",
                {"path": "Writer/report.txt", "start_line": 1, "end_line": 1},
                {"path": "Writer/report.txt", "start_line": 1, "end_line": 1},
            ),
            (
                "grep",
                "/api/ctxfs/grep",
                {"pattern": "remote", "prefix": "Writer"},
                {"pattern": "remote", "prefix": "Writer"},
            ),
        ]
        initial = _assert_rest_mcp_parity(endpoint, reader.token, operations)
        assert initial["read"]["text"] == "hello from the remote tracker\n"

        # Mutate through the real tracker, then exercise the complete REST/MCP
        # matrix again. This catches stale reads in either public transport.
        (tracked / "notes.md").write_text("updated remote context\nsecond line\n")
        (tracked / "report.txt").rename(tracked / "moved-report.md")
        changed = _run_module(
            "ctxd.tracker_service",
            "sync-once",
            environment=_client_environment(writer_home, endpoint, writer.token),
        )
        assert changed.returncode == 0, changed.stderr
        changed_operations = [
            (
                "stat",
                "/api/ctxfs/stat",
                {"path": "Writer/notes.md"},
                {"path": "Writer/notes.md"},
            ),
            ("ls", "/api/ctxfs/ls", {"path": "Writer"}, {"path": "Writer"}),
            ("tree", "/api/ctxfs/tree", {"prefix": "Writer"}, {"prefix": "Writer"}),
            (
                "glob",
                "/api/ctxfs/glob",
                {"pattern": "Writer/*.md"},
                {"pattern": "Writer/*.md"},
            ),
            (
                "read",
                "/api/ctxfs/read",
                {"path": "Writer/notes.md"},
                {"path": "Writer/notes.md"},
            ),
            (
                "read_lines",
                "/api/ctxfs/read-lines",
                {"path": "Writer/moved-report.md", "start_line": 1, "end_line": 1},
                {"path": "Writer/moved-report.md", "start_line": 1, "end_line": 1},
            ),
            (
                "grep",
                "/api/ctxfs/grep",
                {"pattern": "updated", "prefix": "Writer"},
                {"pattern": "updated", "prefix": "Writer"},
            ),
        ]
        changed_results = _assert_rest_mcp_parity(
            endpoint, reader.token, changed_operations
        )
        assert (
            changed_results["read"]["text"] == "updated remote context\nsecond line\n"
        )
        assert {item["path"] for item in changed_results["tree"]["items"]} == {
            "Writer/moved-report.md",
            "Writer/notes.md",
        }
        assert {item["path"] for item in changed_results["grep"]["items"]} == {
            "Writer/notes.md"
        }
        assert (
            httpx.get(
                f"{endpoint}/api/ctxfs/stat",
                params={"path": "Writer/report.txt"},
                headers=_auth(reader.token),
                timeout=2,
            ).status_code
            == 404
        )
        stale_stat_mcp = _mcp_request(
            endpoint,
            reader.token,
            "tools/call",
            params={"name": "stat", "arguments": {"path": "Writer/report.txt"}},
        )
        assert stale_stat_mcp["result"]["isError"] is True

        (tracked / "moved-report.md").unlink()
        (tracked / "persistent.md").write_text("persistent context\n")
        deleted = _run_module(
            "ctxd.tracker_service",
            "sync-once",
            environment=_client_environment(writer_home, endpoint, writer.token),
        )
        assert deleted.returncode == 0, deleted.stderr
        deleted_operations = [
            item
            if item[0] != "read_lines"
            else (
                "read_lines",
                "/api/ctxfs/read-lines",
                {"path": "Writer/notes.md", "start_line": 1, "end_line": 1},
                {"path": "Writer/notes.md", "start_line": 1, "end_line": 1},
            )
            for item in changed_operations
        ]
        deleted_results = _assert_rest_mcp_parity(
            endpoint, reader.token, deleted_operations
        )
        assert {item["path"] for item in deleted_results["tree"]["items"]} == {
            "Writer/notes.md",
            "Writer/persistent.md",
        }
        assert {item["path"] for item in deleted_results["ls"]["items"]} == {
            "Writer/notes.md",
            "Writer/persistent.md",
        }
        assert {item["path"] for item in deleted_results["glob"]["items"]} == {
            "Writer/notes.md",
            "Writer/persistent.md",
        }
        deleted_content = _assert_rest_mcp_parity(
            endpoint,
            reader.token,
            [
                (
                    "grep",
                    "/api/ctxfs/grep",
                    {"pattern": "remote needle", "prefix": "Writer"},
                    {"pattern": "remote needle", "prefix": "Writer"},
                )
            ],
        )
        assert deleted_content["grep"]["items"] == []
        assert (
            httpx.get(
                f"{endpoint}/api/ctxfs/read",
                params={"path": "Writer/moved-report.md"},
                headers=_auth(reader.token),
                timeout=2,
            ).status_code
            == 404
        )
        stale_read_mcp = _mcp_request(
            endpoint,
            reader.token,
            "tools/call",
            params={
                "name": "read",
                "arguments": {"path": "Writer/moved-report.md"},
            },
        )
        assert stale_read_mcp["result"]["isError"] is True

        bounded = httpx.get(
            f"{endpoint}/api/ctxfs/tree",
            params={"prefix": "Writer", "limit": 1},
            headers=_auth(reader.token),
            timeout=2,
        )
        assert bounded.status_code == 200
        assert bounded.json()["complete"] is False
        assert bounded.json()["stopped_by"] == "limit"
        truncated = httpx.get(
            f"{endpoint}/api/ctxfs/read",
            params={"path": "Writer/notes.md", "max_bytes": 5},
            headers=_auth(reader.token),
            timeout=2,
        )
        assert truncated.status_code == 200
        assert truncated.json()["text"] == "updat"
        assert truncated.json()["complete"] is False
        assert truncated.json()["stopped_by"] == "max_bytes"
        assert (
            httpx.get(
                f"{endpoint}/api/ctxfs/stat",
                params={"path": "Writer/missing.md"},
                headers=_auth(reader.token),
                timeout=2,
            ).status_code
            == 404
        )
        assert (
            httpx.get(
                f"{endpoint}/api/ctxfs/tree",
                params={"limit": 0},
                headers=_auth(reader.token),
                timeout=2,
            ).status_code
            == 422
        )
        missing_mcp = _mcp_request(
            endpoint,
            reader.token,
            "tools/call",
            params={"name": "stat", "arguments": {"path": "Writer/missing.md"}},
        )
        assert missing_mcp["result"]["isError"] is True

        assert (
            httpx.get(
                f"{endpoint}/api/ctxfs/tree", headers=_auth(writer.token), timeout=2
            ).status_code
            == 403
        )
        assert (
            httpx.post(
                f"{endpoint}/api/ctxfs/submissions",
                headers=_auth(reader.token),
                json=_submission("Writer"),
                timeout=2,
            ).status_code
            == 403
        )
        assert (
            httpx.post(
                f"{endpoint}/api/ctxfs/submissions",
                headers=_auth(writer.token),
                json=_submission("Other"),
                timeout=2,
            ).status_code
            == 403
        )


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
    first_server_output: dict[str, str] = {}
    with _server(root, port, output=first_server_output) as endpoint:
        health = httpx.get(f"{endpoint}/health", headers=_auth(status.token), timeout=2)
        assert health.status_code == 200
        assert health.json()["ok"] is True
        denial_bodies: list[str] = []
        for authorization in (
            None,
            "Bearer",
            "Basic invalid",
            "Bearer wrong",
            f"Bearer {expired.token}",
            f"Bearer {revoked.token}",
        ):
            headers = {} if authorization is None else {"Authorization": authorization}
            response = httpx.get(
                f"{endpoint}/api/ctxfs/tree", headers=headers, timeout=2
            )
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            denial_bodies.append(response.text)

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
        assert (
            httpx.get(
                f"{endpoint}/api/ctxfs/tree", headers=_auth(reader.token), timeout=2
            ).status_code
            == 401
        )
        tree = httpx.get(
            f"{endpoint}/api/ctxfs/tree", headers=_auth(replacement.token), timeout=2
        )
        assert tree.status_code == 200
        assert [item["path"] for item in tree.json()["items"]].count(
            "Writer/manual.md"
        ) == 1
        assert (
            httpx.post(
                f"{endpoint}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                timeout=2,
            ).status_code
            == 401
        )

    # Store and credential state survive a clean server recovery.
    second_server_output: dict[str, str] = {}
    with _server(root, port, output=second_server_output) as endpoint:
        assert (
            httpx.get(
                f"{endpoint}/health", headers=_auth(status.token), timeout=2
            ).status_code
            == 200
        )
        recovered = httpx.get(
            f"{endpoint}/api/ctxfs/read",
            params={"path": "Writer/manual.md"},
            headers=_auth(replacement.token),
            timeout=2,
        )
        assert recovered.status_code == 200
        assert recovered.json()["text"] == "network context\n"
        assert (
            httpx.post(
                f"{endpoint}/mcp",
                headers=_auth(writer.token),
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                timeout=2,
            ).status_code
            == 403
        )

    # Tokens are one-time bearer secrets: neither API/status output, server
    # output, denial text, nor the persistent database may disclose them.
    observable = json.dumps(health.json()) + json.dumps(tree.json())
    observable += "".join(denial_bodies)
    observable += first_server_output["stdout"] + first_server_output["stderr"]
    observable += second_server_output["stdout"] + second_server_output["stderr"]
    database = (root / "credentials.sqlite3").read_bytes()
    for issued in (writer, reader, status, expired, revoked, replacement):
        assert issued.token not in observable
        assert issued.token.encode() not in database


def test_disjoint_writers_and_admin_capabilities(tmp_path: Path) -> None:
    root = tmp_path / "multi-writer-server"
    with CredentialStore(root) as credentials:
        writer_a = credentials.create(
            name="writer-a", scopes=["submit"], prefixes=["TeamA"]
        )
        writer_b = credentials.create(
            name="writer-b", scopes=["submit"], prefixes=["TeamB"]
        )
        admin = credentials.create(name="administrator", scopes=["admin"])

    with _server(root, _free_port()) as endpoint:
        for issued, prefix in ((writer_a, "TeamA"), (writer_b, "TeamB")):
            accepted = httpx.post(
                f"{endpoint}/api/ctxfs/submissions",
                headers=_auth(issued.token),
                json=_submission(prefix),
                timeout=2,
            )
            assert accepted.status_code == 200
            other = "TeamB" if prefix == "TeamA" else "TeamA"
            denied = httpx.post(
                f"{endpoint}/api/ctxfs/submissions",
                headers=_auth(issued.token),
                json=_submission(other, run_id=f"cross-{prefix}"),
                timeout=2,
            )
            assert denied.status_code == 403

        admin_submission = httpx.post(
            f"{endpoint}/api/ctxfs/submissions",
            headers=_auth(admin.token),
            json=_submission("Operations", run_id="admin-run"),
            timeout=2,
        )
        assert admin_submission.status_code == 200
        assert (
            httpx.get(
                f"{endpoint}/health", headers=_auth(admin.token), timeout=2
            ).status_code
            == 200
        )
        tree = httpx.get(
            f"{endpoint}/api/ctxfs/tree", headers=_auth(admin.token), timeout=2
        )
        assert tree.status_code == 200
        assert {item["path"] for item in tree.json()["items"]} >= {
            "TeamA/manual.md",
            "TeamB/manual.md",
            "Operations/manual.md",
        }
        assert (
            httpx.post(
                f"{endpoint}/mcp",
                headers=_auth(admin.token),
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                timeout=2,
            ).status_code
            == 200
        )


def test_https_server_and_cli_verify_certificate_and_hostname(tmp_path: Path) -> None:
    root = tmp_path / "tls-server"
    certificate, private_key = _certificate(tmp_path)
    with CredentialStore(root) as credentials:
        admin = credentials.create(name="admin", scopes=["admin"])
        reader = credentials.create(name="reader", scopes=["read"])

    port = _free_port()
    with _server(
        root, port, tls_cert=certificate, tls_key=private_key
    ) as bound_endpoint:
        endpoint = f"https://localhost:{port}"
        trusted_tls = ssl.create_default_context(cafile=str(certificate))
        submitted = httpx.post(
            f"{endpoint}/api/ctxfs/submissions",
            headers=_auth(admin.token),
            json=_submission("Secure"),
            verify=trusted_tls,
            timeout=2,
        )
        assert submitted.status_code == 200

        reader_home = tmp_path / "tls-reader-only"
        environment = _client_environment(reader_home, endpoint, reader.token)
        environment["SSL_CERT_FILE"] = str(certificate)
        read = _run_module(
            "ctxd.cli",
            "files",
            "read",
            "Secure/manual.md",
            environment=environment,
        )
        assert read.returncode == 0, read.stderr
        assert read.stdout == "network context\n"
        assert reader.token not in read.stdout + read.stderr

        with pytest.raises(httpx.ConnectError):
            httpx.get(
                f"{endpoint}/api/ctxfs/tree",
                headers=_auth(reader.token),
                timeout=2,
            )
        # Trusting the CA is insufficient when the requested hostname is not
        # present in the certificate's SAN extension.
        with pytest.raises(httpx.ConnectError):
            httpx.get(
                f"{bound_endpoint}/api/ctxfs/tree",
                headers=_auth(reader.token),
                verify=trusted_tls,
                timeout=2,
            )


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
