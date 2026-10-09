from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ctxd.local_ctxfs_service import LocalCtxfsPaths, ensure_started, status


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
    ), patch(
        "ctxd.local_ctxfs_service.time.monotonic", side_effect=[0, 0, 6]
    ):
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
