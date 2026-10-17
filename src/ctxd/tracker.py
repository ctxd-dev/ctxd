from __future__ import annotations

import hashlib
import json
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ctxd.config import resolve_ctxfs_api_key, resolve_ctxfs_endpoint
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


@dataclass(frozen=True)
class FolderScan:
    current: dict[str, "TrackedFile"]
    operations: list[dict[str, Any]]
    complete: bool


@dataclass(frozen=True)
class TrackedFile:
    source_version: str
    path: str | None = None
    file_format: str | None = None
    content_hash: str | None = None


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
    current: dict[str, dict[str, TrackedFile]] = {}
    submissions: list[tuple[str, str, list[dict[str, Any]]]] = []
    current_prefixes = {folder.prefix for folder in folders}

    for folder in folders:
        previous_files = previous.get(folder.prefix, {})
        scan = _scan_folder(folder, previous_files=previous_files)
        folder_current = dict(previous_files) if not scan.complete else {}
        folder_current.update(scan.current)
        current[folder.prefix] = folder_current
        operations = list(scan.operations)
        if scan.complete:
            known_source_keys = set(scan.current)
            for source_key, tracked_file in previous_files.items():
                if source_key not in known_source_keys:
                    operations.append(
                        {
                            "kind": "delete",
                            "source_key": source_key,
                            "source_version": tracked_file.source_version,
                        }
                    )
        if operations:
            submissions.append((folder.prefix, _writer_id(folder), operations))

    for prefix, files in previous.items():
        if prefix in current_prefixes:
            continue
        operations = [
            {
                "kind": "delete",
                "source_key": source_key,
                "source_version": tracked_file.source_version,
            }
            for source_key, tracked_file in files.items()
        ]
        if operations:
            submissions.append((prefix, _writer_id_for_prefix(prefix), operations))

    submitted = 0
    for writer_prefix, writer_id, operations in submissions:
        _submit_operations(
            resolved_endpoint,
            writer_id=writer_id,
            writer_prefix=writer_prefix,
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
        try:
            sync_once()
        except Exception as exc:
            print(f"ctxd tracker sync failed: {exc}", flush=True)
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        time.sleep(interval_seconds)


def _scan_folder(
    folder: FolderConfig,
    *,
    previous_files: dict[str, TrackedFile] | None = None,
) -> FolderScan:
    root = Path(folder.path).expanduser()
    previous_files = previous_files or {}
    current: dict[str, TrackedFile] = {}
    operations: list[dict[str, Any]] = []
    complete = True
    if not root.exists() or not root.is_dir():
        return FolderScan(current=current, operations=operations, complete=False)

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name.startswith(".") or path.suffix.lower() not in TRACKED_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            complete = False
            continue
        if stat.st_size > MAX_TRACKED_FILE_BYTES:
            continue
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            complete = False
            continue

        relative_path = path.relative_to(root).as_posix()
        source_key = _source_key(folder, stat)
        source_version = _source_version(data)
        ctxfs_path = f"{folder.prefix.rstrip('/')}/{relative_path}"
        file_format = path.suffix.lower().lstrip(".") or "text"
        tracked_file = TrackedFile(
            source_version=source_version,
            path=ctxfs_path,
            file_format=file_format,
            content_hash=_ctxfs_content_hash(text),
        )
        current[source_key] = tracked_file
        previous_file = previous_files.get(source_key)
        if previous_file == tracked_file:
            continue
        if (
            previous_file is not None
            and previous_file.source_version == source_version
            and previous_file.path is not None
            and previous_file.path != ctxfs_path
            and previous_file.file_format == file_format
            and previous_file.content_hash is not None
        ):
            operations.append(
                {
                    "kind": "move",
                    "source_key": source_key,
                    "path": ctxfs_path,
                    "source_version": source_version,
                    "expected_content_hash": previous_file.content_hash,
                }
            )
            continue
        operations.append(
            {
                "kind": "put",
                "path": ctxfs_path,
                "source_key": source_key,
                "source_version": tracked_file.source_version,
                "doc_class": "editable",
                "file_format": file_format,
                "text": text,
                "provenance": {
                    "producer": "ctxd-tracker",
                    "producer_version": "1",
                },
            }
        )
    return FolderScan(current=current, operations=operations, complete=complete)


def _source_key(folder: FolderConfig, stat) -> str:
    return f"{folder.prefix}:{stat.st_dev}:{stat.st_ino}"


def _source_version(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ctxfs_content_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return _source_version(normalized.encode("utf-8"))


def _writer_id(folder: FolderConfig) -> str:
    return _writer_id_for_prefix(folder.prefix)


def _writer_id_for_prefix(prefix: str) -> str:
    return f"ctxd-tracker:{prefix}"


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
    api_key = resolve_ctxfs_api_key(endpoint=endpoint)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    if endpoint.startswith("unix://"):
        socket_path = endpoint.removeprefix("unix://")
        transport = httpx.HTTPTransport(uds=socket_path)
        with httpx.Client(
            base_url="http://ctxfs.local",
            transport=transport,
            timeout=30.0,
        ) as client:
            response = client.post(
                "/api/ctxfs/submissions", json=payload, headers=headers
            )
    else:
        with httpx.Client(base_url=endpoint.rstrip("/"), timeout=30.0) as client:
            response = client.post(
                "/api/ctxfs/submissions", json=payload, headers=headers
            )
    response.raise_for_status()


def _load_state(path: Path) -> dict[str, dict[str, TrackedFile]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, TrackedFile]] = {}
    for prefix, files in payload.items():
        if not isinstance(prefix, str) or not isinstance(files, dict):
            continue
        tracked_files: dict[str, TrackedFile] = {}
        for source_key, value in files.items():
            if not isinstance(source_key, str):
                continue
            if isinstance(value, str):
                tracked_files[source_key] = TrackedFile(source_version=value)
                continue
            if not isinstance(value, dict):
                continue
            source_version = value.get("source_version")
            tracked_path = value.get("path")
            file_format = value.get("file_format")
            content_hash = value.get("content_hash")
            if not isinstance(source_version, str):
                continue
            tracked_files[source_key] = TrackedFile(
                source_version=source_version,
                path=tracked_path if isinstance(tracked_path, str) else None,
                file_format=file_format if isinstance(file_format, str) else None,
                content_hash=content_hash if isinstance(content_hash, str) else None,
            )
        result[prefix] = tracked_files
    return result


def _save_state(path: Path, state: dict[str, dict[str, TrackedFile]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        prefix: {
            source_key: {
                "source_version": tracked_file.source_version,
                "path": tracked_file.path,
                "file_format": tracked_file.file_format,
                "content_hash": tracked_file.content_hash,
            }
            for source_key, tracked_file in files.items()
        }
        for prefix, files in state.items()
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
