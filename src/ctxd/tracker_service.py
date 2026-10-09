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

from ctxd.folders import list_folders
from ctxd.local_ctxfs_service import ensure_started
from ctxd.tracker import run_forever, sync_once

DEFAULT_LOCAL_HOME = Path.home() / ".ctxd" / "local"
DEFAULT_PID_FILE_NAME = "tracker.pid"
PROCESS_MARKER = "ctxd.tracker_service"


@dataclass(frozen=True)
class TrackerServicePaths:
    local_home: Path
    pid_file: Path


def default_paths(local_home: Path | None = None) -> TrackerServicePaths:
    if local_home is None:
        local_home = DEFAULT_LOCAL_HOME
    local_home = local_home.expanduser()
    return TrackerServicePaths(
        local_home=local_home,
        pid_file=local_home / DEFAULT_PID_FILE_NAME,
    )


def status(paths: TrackerServicePaths | None = None) -> dict[str, object]:
    if paths is None:
        paths = default_paths()
    pid = _read_pid(paths.pid_file)
    running = _pid_matches(pid, PROCESS_MARKER) if pid is not None else False
    return {
        "running": running,
        "pid": pid,
        "folders": len(list_folders()),
    }


def start(paths: TrackerServicePaths | None = None) -> dict[str, object]:
    if paths is None:
        paths = default_paths()
    current = status(paths)
    if current["running"]:
        return current

    ensure_started()
    paths.local_home.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "ctxd.tracker_service", "run"]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _write_pid(paths.pid_file, process.pid)
    return status(paths)


def stop(paths: TrackerServicePaths | None = None) -> dict[str, object]:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ctxd.tracker_service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--interval", type=float, default=10.0)
    subparsers.add_parser("sync-once")

    args = parser.parse_args(argv)
    if args.command == "run":
        run_forever(interval_seconds=args.interval)
        return 0
    if args.command == "sync-once":
        sync_once()
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


def _cleanup_runtime_files(paths: TrackerServicePaths) -> None:
    try:
        paths.pid_file.unlink()
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
