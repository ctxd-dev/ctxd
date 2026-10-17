from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

import uvicorn

from ctxd.ctxfs_client import CtxfsClient
from ctxd.local_ctxfs_server import (
    CtxfsAuthMode,
    DEFAULT_CTXFS_HOST,
    DEFAULT_CTXFS_PORT,
    create_ctxfs_app,
    default_ctxfs_root,
)

DEFAULT_LOCAL_HOME = Path.home() / ".ctxd" / "local"
DEFAULT_CTXFS_SOCKET_NAME = "ctxfs.sock"
DEFAULT_PID_FILE_NAME = "ctxfs.pid"
DEFAULT_LOCK_FILE_NAME = "ctxfs.lock"
PROCESS_MARKER = "ctxd.local_ctxfs_service"


@dataclass(frozen=True)
class LocalCtxfsPaths:
    local_home: Path
    root: Path
    socket_path: Path
    pid_file: Path
    lock_file: Path

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
        lock_file=local_home / DEFAULT_LOCK_FILE_NAME,
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

    _ensure_private_directory(paths.local_home)
    with _exclusive_lock(paths.lock_file):
        current = status(paths)
        if current["healthy"]:
            return current
        if current["running"]:
            stop(paths)

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


def serve(
    root: Path,
    *,
    socket_path: Path | None = None,
    host: str = DEFAULT_CTXFS_HOST,
    port: int = DEFAULT_CTXFS_PORT,
    allow_network: bool = False,
    tls_cert: Path | None = None,
    tls_key: Path | None = None,
    credential_root: Path | None = None,
    advertised_url: str | None = None,
) -> None:
    """Serve ctxfs in the foreground using an explicitly safe transport policy."""
    root = root.expanduser()
    socket_path = (
        socket_path.expanduser().resolve() if socket_path is not None else None
    )
    tls_cert = tls_cert.expanduser() if tls_cert is not None else None
    tls_key = tls_key.expanduser() if tls_key is not None else None
    credential_root = (
        credential_root.expanduser() if credential_root is not None else None
    )
    host = host.strip()
    if not host:
        raise ValueError("host must not be empty")
    if socket_path is not None and host != DEFAULT_CTXFS_HOST:
        raise ValueError("--socket and --host cannot be used together")
    if socket_path is not None and (tls_cert is not None or tls_key is not None):
        raise ValueError("TLS options cannot be used with a Unix socket")
    if socket_path is not None and allow_network:
        raise ValueError("--allow-network cannot be used with a Unix socket")
    if socket_path is not None and credential_root is not None:
        raise ValueError("--credential-root cannot be used with a trusted Unix socket")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    is_loopback = _is_loopback_host(host)
    if socket_path is None and not is_loopback:
        if not allow_network:
            raise ValueError(
                "non-loopback ctxfs serving requires explicit --allow-network"
            )
        if tls_cert is None or tls_key is None:
            raise ValueError(
                "non-loopback ctxfs serving requires --tls-cert and --tls-key"
            )
    if (tls_cert is None) != (tls_key is None):
        raise ValueError("--tls-cert and --tls-key must be provided together")
    if tls_cert is not None and tls_key is not None:
        _validate_tls_file(tls_cert, "TLS certificate")
        _validate_tls_file(tls_key, "TLS private key", private=True)

    if advertised_url is not None:
        _validate_advertised_url(
            advertised_url,
            socket_path=socket_path,
            tls_enabled=tls_cert is not None,
            port=port,
        )

    _ensure_private_directory(root)
    # Trust is a property of the private managed Unix socket, never of TCP.
    auth_mode = (
        CtxfsAuthMode.TRUSTED_LOCAL if socket_path is not None else CtxfsAuthMode.BEARER
    )
    if credential_root is None:
        app = create_ctxfs_app(root, auth_mode=auth_mode)
    else:
        app = create_ctxfs_app(
            root, auth_mode=auth_mode, credential_root=credential_root
        )
    if advertised_url is not None:
        app.state.ctxfs_advertised_url = advertised_url
    if socket_path is not None:
        _ensure_private_socket_directory(socket_path.parent)
        _validate_socket_target(socket_path)
        with _private_umask():
            uvicorn.run(
                app,
                uds=str(socket_path),
                log_level="warning",
                proxy_headers=False,
            )
    else:
        run_kwargs: dict[str, object] = {
            "host": host,
            "port": port,
            "log_level": "warning",
            "proxy_headers": False,
        }
        if tls_cert is not None and tls_key is not None:
            run_kwargs.update(ssl_certfile=str(tls_cert), ssl_keyfile=str(tls_key))
        uvicorn.run(app, **run_kwargs)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_tls_file(path: Path, label: str, *, private: bool = False) -> None:
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ValueError(f"{label} must be a readable regular file: {path}")
    if private and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(
            f"{label} must not be accessible by group or other users: {path}"
        )


def _validate_advertised_url(
    value: str, *, socket_path: Path | None, tls_enabled: bool, port: int
) -> None:
    parsed = urlparse(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("advertised URL must not contain credentials")
    if parsed.query or parsed.fragment or parsed.params:
        raise ValueError(
            "advertised URL must not contain parameters, a query, or a fragment"
        )
    if socket_path is not None:
        if parsed.scheme != "unix" or parsed.netloc or Path(parsed.path) != socket_path:
            raise ValueError(
                "a Unix-socket server must advertise its exact unix:// path"
            )
        return
    expected_scheme = "https" if tls_enabled else "http"
    if parsed.scheme != expected_scheme or parsed.hostname is None:
        raise ValueError(f"TCP server advertised URL must use {expected_scheme}://")
    try:
        advertised_port = parsed.port
    except ValueError as exc:
        raise ValueError("advertised URL contains an invalid port") from exc
    effective_port = advertised_port
    if effective_port is None:
        effective_port = 443 if expected_scheme == "https" else 80
    if effective_port != port:
        raise ValueError(f"advertised URL must use the listening port {port}")
    if parsed.path not in {"", "/"}:
        raise ValueError("advertised URL must not contain a path")
    if not tls_enabled and not _is_loopback_host(parsed.hostname):
        raise ValueError("a plaintext server may advertise only a loopback URL")


def _validate_socket_target(path: Path) -> None:
    """Never replace an existing filesystem object or live socket implicitly."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    kind = "Unix socket" if stat.S_ISSOCK(mode) else "filesystem object"
    raise ValueError(f"refusing to replace existing {kind}: {path}")


def _ensure_private_socket_directory(path: Path) -> None:
    """Create a private parent, but never chmod a caller-owned directory."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        path.mkdir(parents=True, mode=0o700)
        return
    if not stat.S_ISDIR(mode):
        raise ValueError(f"Unix-socket parent must be a directory: {path}")
    if stat.S_IMODE(mode) & 0o077:
        raise ValueError(
            f"Unix-socket parent must not be accessible by group or other users: {path}"
        )


@contextlib.contextmanager
def _private_umask():
    # Remove group/other access while retaining owner execute bits required to
    # traverse directories created by ctxfs during request handling.
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ctxd.local_ctxfs_service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--root", type=Path, required=True)
    listener = serve_parser.add_mutually_exclusive_group()
    listener.add_argument("--socket", type=Path)
    listener.add_argument("--host", default=DEFAULT_CTXFS_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_CTXFS_PORT)
    serve_parser.add_argument("--allow-network", action="store_true")
    serve_parser.add_argument("--tls-cert", type=Path)
    serve_parser.add_argument("--tls-key", type=Path)
    serve_parser.add_argument("--credential-root", type=Path)
    serve_parser.add_argument("--advertised-url")

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(
            args.root,
            socket_path=args.socket,
            host=args.host,
            port=args.port,
            allow_network=args.allow_network,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
            credential_root=args.credential_root,
            advertised_url=args.advertised_url,
        )
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


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("local ctxfs service locking requires Unix fcntl") from exc

    with path.open("a+") as lock_file:
        path.chmod(0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    raise SystemExit(main())
