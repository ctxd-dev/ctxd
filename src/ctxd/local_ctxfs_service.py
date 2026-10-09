from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import uvicorn

from ctxd.ctxfs_client import CtxfsClient
from ctxd.local_ctxfs_server import (
    DEFAULT_CTXFS_HOST,
    DEFAULT_CTXFS_PORT,
    create_ctxfs_app,
    default_ctxfs_root,
)

DEFAULT_LOCAL_HOME = Path.home() / ".ctxd" / "local"
DEFAULT_CTXFS_SOCKET_NAME = "ctxfs.sock"
DEFAULT_PID_FILE_NAME = "ctxfs.pid"
PROCESS_MARKER = "ctxd.local_ctxfs_service"


@dataclass(frozen=True)
class LocalCtxfsPaths:
    local_home: Path
    root: Path
    socket_path: Path
    pid_file: Path

    @property
    def endpoint(self) -> str:
        return f"unix://{self.socket_path}"


def default_paths(local_home: Path | None = None) -> LocalCtxfsPaths:
    if local_home is None:
        local_home = DEFAULT_LOCAL_HOME
    local_home = local_home.expanduser()
    return LocalCtxfsPaths(
        local_home=local_home,
        root=default_ctxfs_root(local_home),
        socket_path=local_home / DEFAULT_CTXFS_SOCKET_NAME,
        pid_file=local_home / DEFAULT_PID_FILE_NAME,
    )


def status(paths: LocalCtxfsPaths | None = None) -> dict[str, object]:
    if paths is None:
        paths = default_paths()

    pid = _read_pid(paths.pid_file)
    running = _pid_matches(pid, PROCESS_MARKER) if pid is not None else False
    healthy = False
    error: str | None = None

    if running:
        try:
            CtxfsClient(endpoint=paths.endpoint, timeout=2.0).status()
            healthy = True
        except Exception as exc:  # health payload for CLI status only
            error = str(exc)

    return {
        "running": running,
        "healthy": healthy,
        "pid": pid,
        "endpoint": paths.endpoint,
        "root": str(paths.root),
        "error": error,
    }


def ensure_started(paths: LocalCtxfsPaths | None = None) -> dict[str, object]:
    if paths is None:
        paths = default_paths()

    current = status(paths)
    if current["healthy"]:
        return current
    if current["running"]:
        stop(paths)

    _ensure_private_directory(paths.local_home)
    _ensure_private_directory(paths.root)

    if paths.socket_path.exists():
        paths.socket_path.unlink()

    command = [
        sys.executable,
        "-m",
        "ctxd.local_ctxfs_service",
        "serve",
        "--root",
        str(paths.root),
        "--socket",
        str(paths.socket_path),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _write_pid(paths.pid_file, process.pid)

    deadline = time.monotonic() + 5
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            CtxfsClient(endpoint=paths.endpoint, timeout=1.0).status()
            return status(paths)
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)

    _terminate_process(process)
    _cleanup_runtime_files(paths)
    raise RuntimeError(
        f"ctxfs service did not become healthy at {paths.endpoint}: {last_error}"
    )


def stop(paths: LocalCtxfsPaths | None = None) -> dict[str, object]:
    if paths is None:
        paths = default_paths()

    pid = _read_pid(paths.pid_file)
    if pid is not None and _pid_matches(pid, PROCESS_MARKER):
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not _pid_matches(pid, PROCESS_MARKER):
                break
            time.sleep(0.1)
        if _pid_matches(pid, PROCESS_MARKER):
            os.kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if not _pid_matches(pid, PROCESS_MARKER):
                    break
                time.sleep(0.1)

    _cleanup_runtime_files(paths)
    return status(paths)


def serve(root: Path, *, socket_path: Path | None = None) -> None:
    _ensure_private_directory(root)
    app = create_ctxfs_app(root)
    if socket_path is not None:
        _ensure_private_directory(socket_path.parent)
        uvicorn.run(app, uds=str(socket_path), log_level="warning")
    else:
        uvicorn.run(
            app,
            host=DEFAULT_CTXFS_HOST,
            port=DEFAULT_CTXFS_PORT,
            log_level="warning",
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ctxd.local_ctxfs_service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--root", type=Path, required=True)
    serve_parser.add_argument("--socket", type=Path)

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.root, socket_path=args.socket)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text().strip()
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            return int(raw)
        except ValueError:
            return None
    pid = payload.get("pid") if isinstance(payload, dict) else None
    return pid if isinstance(pid, int) else None


def _write_pid(path: Path, pid: int) -> None:
    path.write_text(json.dumps({"pid": pid, "marker": PROCESS_MARKER}) + "\n")
    path.chmod(0o600)


def _pid_is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_matches(pid: int | None, marker: str) -> bool:
    if not _pid_is_running(pid):
        return False
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return marker in command


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _cleanup_runtime_files(paths: LocalCtxfsPaths) -> None:
    for path in (paths.pid_file, paths.socket_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


if __name__ == "__main__":
    raise SystemExit(main())
