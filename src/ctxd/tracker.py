from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ctxd.config import resolve_ctxfs_endpoint
from ctxd.folders import FolderConfig, list_folders

MAX_TRACKED_FILE_BYTES = 5_000_000
TRACKED_SUFFIXES = {
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".log",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class TrackerPaths:
    state_file: Path


def default_tracker_paths(local_home: Path | None = None) -> TrackerPaths:
    if local_home is None:
        local_home = Path.home() / ".ctxd" / "local"
    return TrackerPaths(state_file=local_home.expanduser() / "tracker-state.json")


def sync_once(
    *,
    folders: list[FolderConfig] | None = None,
    endpoint: str | None = None,
    paths: TrackerPaths | None = None,
) -> dict[str, int]:
    if folders is None:
        folders = list_folders()
    if paths is None:
        paths = default_tracker_paths()
    resolved_endpoint = endpoint or resolve_ctxfs_endpoint()
    previous = _load_state(paths.state_file)
    current: dict[str, dict[str, str]] = {}
    operations_by_folder: dict[str, list[dict[str, Any]]] = {}

    for folder in folders:
        folder_current, operations = _scan_folder(folder)
        current[folder.prefix] = folder_current
        known_source_keys = set(folder_current)
        for source_key, source_version in previous.get(folder.prefix, {}).items():
            if source_key not in known_source_keys:
                operations.append(
                    {
                        "kind": "delete",
                        "source_key": source_key,
                        "source_version": source_version,
                    }
                )
        if operations:
            operations_by_folder[folder.name] = operations

    submitted = 0
    for folder in folders:
        operations = operations_by_folder.get(folder.name)
        if not operations:
            continue
        _submit_operations(
            resolved_endpoint,
            writer_id=_writer_id(folder),
            writer_prefix=folder.prefix,
            operations=operations,
        )
        submitted += len(operations)

    _save_state(paths.state_file, current)
    return {
        "folders": len(folders),
        "operations": submitted,
    }


def run_forever(interval_seconds: float = 10.0) -> None:
    while True:
        sync_once()
        time.sleep(interval_seconds)


def _scan_folder(folder: FolderConfig) -> tuple[dict[str, str], list[dict[str, Any]]]:
    root = Path(folder.path).expanduser()
    current: dict[str, str] = {}
    operations: list[dict[str, Any]] = []
    if not root.exists():
        return current, operations

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name.startswith(".") or path.suffix.lower() not in TRACKED_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size > MAX_TRACKED_FILE_BYTES:
            continue
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        relative_path = path.relative_to(root).as_posix()
        source_key = _source_key(folder, stat)
        source_version = _source_version(data)
        current[source_key] = source_version
        operations.append(
            {
                "kind": "put",
                "path": f"{folder.prefix.rstrip('/')}/{relative_path}",
                "source_key": source_key,
                "source_version": source_version,
                "doc_class": "editable",
                "file_format": path.suffix.lower().lstrip(".") or "text",
                "text": text,
                "provenance": {
                    "producer": "ctxd-tracker",
                    "producer_version": "1",
                },
            }
        )
    return current, operations


def _source_key(folder: FolderConfig, stat) -> str:
    return f"{folder.prefix}:{stat.st_dev}:{stat.st_ino}"


def _source_version(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _writer_id(folder: FolderConfig) -> str:
    return f"ctxd-tracker:{folder.name}"


def _submit_operations(
    endpoint: str,
    *,
    writer_id: str,
    writer_prefix: str,
    operations: list[dict[str, Any]],
) -> None:
    payload = {
        "writer_id": writer_id,
        "writer_prefix": writer_prefix,
        "submission": {
            "run_id": str(uuid.uuid4()),
            "sequence": 0,
            "is_full_scan": True,
            "operations": operations,
        },
    }
    if endpoint.startswith("unix://"):
        socket_path = endpoint.removeprefix("unix://")
        transport = httpx.HTTPTransport(uds=socket_path)
        with httpx.Client(
            base_url="http://ctxfs.local",
            transport=transport,
            timeout=30.0,
        ) as client:
            response = client.post("/api/ctxfs/submissions", json=payload)
    else:
        with httpx.Client(base_url=endpoint.rstrip("/"), timeout=30.0) as client:
            response = client.post("/api/ctxfs/submissions", json=payload)
    response.raise_for_status()


def _load_state(path: Path) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for prefix, files in payload.items():
        if not isinstance(prefix, str) or not isinstance(files, dict):
            continue
        result[prefix] = {
            source_key: source_version
            for source_key, source_version in files.items()
            if isinstance(source_key, str) and isinstance(source_version, str)
        }
    return result


def _save_state(path: Path, state: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
