from __future__ import annotations

import socket
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ctxd.local_ctxfs_server import CtxfsAuthMode
from ctxd.local_ctxfs_service import (
    LocalCtxfsPaths,
    _is_loopback_host,
    ensure_started,
    serve,
    status,
)


class _Process:
    def __init__(self, pid: int = 456, poll_result=None) -> None:
        self.pid = pid
        self.poll_result = poll_result
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.poll_result

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None):
        del timeout
        self.poll_result = 0
        return 0


def _paths(tmp_path: Path) -> LocalCtxfsPaths:
    return LocalCtxfsPaths(
        local_home=tmp_path / "local",
        root=tmp_path / "ctxfs",
        socket_path=tmp_path / "local" / "ctxfs.sock",
        pid_file=tmp_path / "local" / "ctxfs.pid",
        lock_file=tmp_path / "local" / "ctxfs.lock",
    )


def test_ensure_started_returns_existing_healthy_service(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    paths.pid_file.write_text('{"pid": 123, "marker": "ctxd.local_ctxfs_service"}\n')

    with patch("ctxd.local_ctxfs_service._pid_matches", return_value=True), patch(
        "ctxd.local_ctxfs_service.CtxfsClient"
    ) as client_class, patch("ctxd.local_ctxfs_service.subprocess.Popen") as popen:
        client_class.return_value.status.return_value = object()
        result = ensure_started(paths)

    assert result["running"] is True
    assert result["healthy"] is True
    assert result["endpoint"] == f"unix://{paths.socket_path}"
    popen.assert_not_called()


def test_ensure_started_launches_service_when_unhealthy(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    process = _Process()
    attempts = {"count": 0}

    def status_side_effect():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("not ready")
        return object()

    with patch("ctxd.local_ctxfs_service._pid_matches", return_value=True), patch(
        "ctxd.local_ctxfs_service.CtxfsClient"
    ) as client_class, patch(
        "ctxd.local_ctxfs_service.subprocess.Popen", return_value=process
    ) as popen:
        client_class.return_value.status.side_effect = status_side_effect
        result = ensure_started(paths)

    assert result["pid"] == 456
    assert result["healthy"] is True
    assert '"pid": 456' in paths.pid_file.read_text()
    assert stat.S_IMODE(paths.local_home.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
    command = popen.call_args.args[0]
    assert command[1:4] == ["-m", "ctxd.local_ctxfs_service", "serve"]
    assert str(paths.root) in command
    assert str(paths.socket_path) in command


def test_status_reports_unhealthy_service_error(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    paths.pid_file.write_text('{"pid": 123, "marker": "ctxd.local_ctxfs_service"}\n')

    with patch("ctxd.local_ctxfs_service._pid_matches", return_value=True), patch(
        "ctxd.local_ctxfs_service.CtxfsClient"
    ) as client_class:
        client_class.return_value.status.side_effect = RuntimeError("boom")
        result = status(paths)

    assert result["running"] is True
    assert result["healthy"] is False
    assert result["error"] == "boom"


def test_ensure_started_raises_when_process_never_becomes_healthy(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    paths.socket_path.write_text("")
    process = _Process()

    with patch("ctxd.local_ctxfs_service._pid_matches", return_value=False), patch(
        "ctxd.local_ctxfs_service.CtxfsClient"
    ) as client_class, patch(
        "ctxd.local_ctxfs_service.subprocess.Popen", return_value=process
    ), patch("ctxd.local_ctxfs_service.time.monotonic", side_effect=[0, 0, 6]):
        client_class.return_value.status.side_effect = RuntimeError("not ready")
        with pytest.raises(RuntimeError, match="did not become healthy"):
            ensure_started(paths)

    assert process.terminated is True
    assert not paths.pid_file.exists()
    assert not paths.socket_path.exists()


def test_status_ignores_stale_or_unrelated_pid(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    paths.pid_file.write_text('{"pid": 123, "marker": "ctxd.local_ctxfs_service"}\n')

    with patch("ctxd.local_ctxfs_service._pid_matches", return_value=False), patch(
        "ctxd.local_ctxfs_service.CtxfsClient"
    ) as client_class:
        result = status(paths)

    assert result["running"] is False
    assert result["healthy"] is False
    client_class.assert_not_called()


def test_serve_selects_auth_policy_from_transport(tmp_path: Path) -> None:
    app = object()
    socket_path = tmp_path / "run" / "ctxfs.sock"
    with patch(
        "ctxd.local_ctxfs_service.create_ctxfs_app", return_value=app
    ) as create_app, patch("ctxd.local_ctxfs_service.uvicorn.run") as run:
        serve(tmp_path / "unix-root", socket_path=socket_path)
        create_app.assert_called_once_with(
            tmp_path / "unix-root", auth_mode=CtxfsAuthMode.TRUSTED_LOCAL
        )
        run.assert_called_once_with(
            app,
            uds=str(socket_path),
            log_level="warning",
            proxy_headers=False,
        )

    with patch(
        "ctxd.local_ctxfs_service.create_ctxfs_app", return_value=app
    ) as create_app, patch("ctxd.local_ctxfs_service.uvicorn.run") as run:
        serve(tmp_path / "tcp-root")
        create_app.assert_called_once_with(
            tmp_path / "tcp-root", auth_mode=CtxfsAuthMode.BEARER
        )
        run.assert_called_once_with(
            app,
            host="127.0.0.1",
            port=8765,
            log_level="warning",
            proxy_headers=False,
        )


@pytest.mark.parametrize("host", ["127.0.0.1", "127.2.3.4", "::1", "localhost"])
def test_loopback_host_classification(host: str) -> None:
    assert _is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", "ctxfs.local"])
def test_network_host_classification(host: str) -> None:
    assert _is_loopback_host(host) is False


def test_serve_rejects_remote_bind_without_opt_in_and_tls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--allow-network"):
        serve(tmp_path / "root", host="0.0.0.0")
    with pytest.raises(ValueError, match="requires --tls-cert and --tls-key"):
        serve(tmp_path / "root", host="0.0.0.0", allow_network=True)


def test_serve_rejects_unused_credentials_for_trusted_socket(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--credential-root"):
        serve(
            tmp_path / "root",
            socket_path=tmp_path / "ctxfs.sock",
            credential_root=tmp_path / "credentials",
        )


def test_serve_rejects_missing_or_non_regular_tls_files(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_text("certificate")
    with pytest.raises(ValueError, match="TLS private key"):
        serve(
            tmp_path / "root",
            host="::",
            allow_network=True,
            tls_cert=cert,
            tls_key=tmp_path / "missing-key.pem",
        )


def test_serve_rejects_insecure_private_key_permissions(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate")
    key.write_text("private key")
    key.chmod(0o644)

    with pytest.raises(ValueError, match="group or other users"):
        serve(
            tmp_path / "root",
            host="::",
            allow_network=True,
            tls_cert=cert,
            tls_key=key,
        )


def test_serve_refuses_to_replace_existing_socket_or_file(tmp_path: Path) -> None:
    short_dir = Path(tempfile.mkdtemp(prefix="ctxd-", dir="/tmp"))
    socket_path = short_dir / "ctxfs.sock"
    try:
        with socket.socket(socket.AF_UNIX) as existing:
            existing.bind(str(socket_path))
            with pytest.raises(ValueError, match="existing Unix socket"):
                serve(tmp_path / "root", socket_path=socket_path)
    finally:
        socket_path.unlink(missing_ok=True)
        short_dir.rmdir()

    socket_path = tmp_path / "ctxfs.sock"
    socket_path.write_text("not a socket")
    with pytest.raises(ValueError, match="existing filesystem object"):
        serve(tmp_path / "root", socket_path=socket_path)


def test_serve_rejects_shared_socket_parent_without_changing_it(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)

    with pytest.raises(ValueError, match="group or other users"):
        serve(tmp_path / "root", socket_path=shared / "ctxfs.sock")

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    "advertised_url, message",
    [
        ("http://user:secret@127.0.0.1:8765", "must not contain credentials"),
        ("http://127.0.0.1:9999", "listening port 8765"),
        ("http://ctxfs.example.test:8765", "only a loopback URL"),
        ("http://127.0.0.1:8765/api", "must not contain a path"),
    ],
)
def test_serve_rejects_invalid_advertised_url(
    tmp_path: Path, advertised_url: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        serve(tmp_path / "root", advertised_url=advertised_url)


def test_serve_remote_uses_bearer_auth_and_direct_tls(tmp_path: Path) -> None:
    app = object()
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate")
    key.write_text("private key")
    key.chmod(0o600)
    credentials = tmp_path / "credentials"
    with patch(
        "ctxd.local_ctxfs_service.create_ctxfs_app", return_value=app
    ) as create_app, patch("ctxd.local_ctxfs_service.uvicorn.run") as run:
        serve(
            tmp_path / "root",
            host="::",
            port=9443,
            allow_network=True,
            tls_cert=cert,
            tls_key=key,
            credential_root=credentials,
        )
    create_app.assert_called_once_with(
        tmp_path / "root",
        auth_mode=CtxfsAuthMode.BEARER,
        credential_root=credentials,
    )
    run.assert_called_once_with(
        app,
        host="::",
        port=9443,
        log_level="warning",
        proxy_headers=False,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
    )
