from __future__ import annotations

import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ctxd.config import load_config, save_config

RESERVED_FOLDER_NAMES = {"local-files"}


class FolderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    prefix: str = Field(min_length=1)


def add_folder(path: str, *, name: str | None = None) -> FolderConfig:
    folder_path = Path(path).expanduser().resolve()
    folder_name = name or folder_path.name
    _validate_folder_name(folder_name)
    if not folder_path.exists():
        raise ValueError(f"Folder path `{folder_path}` does not exist.")
    if not folder_path.is_dir():
        raise ValueError(f"Folder path `{folder_path}` is not a directory.")

    config = load_config()
    folders = _folders_from_config(config)
    if folder_name in folders:
        raise ValueError(f"Folder `{folder_name}` already exists.")

    folder = FolderConfig(
        name=folder_name,
        path=str(folder_path),
        prefix=f"local-files/{uuid.uuid4()}",
    )
    folders[folder_name] = folder
    config["folders"] = {
        key: value.model_dump()
        for key, value in sorted(folders.items(), key=lambda item: item[0].lower())
    }
    save_config(config)
    return folder


def list_folders() -> list[FolderConfig]:
    return [
        value
        for _, value in sorted(
            _folders_from_config(load_config()).items(),
            key=lambda item: item[0].lower(),
        )
    ]


def remove_folder(name: str) -> FolderConfig:
    config = load_config()
    folders = _folders_from_config(config)
    try:
        removed = folders.pop(name)
    except KeyError as exc:
        raise ValueError(f"Folder `{name}` does not exist.") from exc

    config["folders"] = {
        key: value.model_dump()
        for key, value in sorted(folders.items(), key=lambda item: item[0].lower())
    }
    save_config(config)
    return removed


def resolve_folder_prefix(name: str) -> str:
    folders = _folders_from_config(load_config())
    try:
        return folders[name].prefix
    except KeyError as exc:
        raise ValueError(f"Folder `{name}` does not exist.") from exc


def resolve_named_path(path: str) -> str:
    first, separator, rest = path.partition("/")
    if not separator:
        return resolve_folder_prefix(first)
    return f"{resolve_folder_prefix(first).rstrip('/')}/{rest.lstrip('/')}"


def try_resolve_named_path(path: str) -> str:
    first, separator, rest = path.partition("/")
    folders = _folders_from_config(load_config())
    folder = folders.get(first)
    if folder is None:
        return path
    if not separator:
        return folder.prefix
    return f"{folder.prefix.rstrip('/')}/{rest.lstrip('/')}"


def _folders_from_config(config: dict) -> dict[str, FolderConfig]:
    raw_folders = config.get("folders")
    if not isinstance(raw_folders, dict):
        return {}

    folders: dict[str, FolderConfig] = {}
    for key, value in raw_folders.items():
        if isinstance(key, str) and isinstance(value, dict):
            folder = FolderConfig.model_validate(value)
            folders[folder.name] = folder
    return folders


def _validate_folder_name(name: str) -> None:
    if not name:
        raise ValueError("Folder name is required.")
    if name in {".", ".."}:
        raise ValueError("Folder name cannot be `.` or `..`.")
    if "/" in name or "\\" in name:
        raise ValueError("Folder name cannot contain path separators.")
    if name in RESERVED_FOLDER_NAMES:
        raise ValueError(f"Folder name `{name}` is reserved.")
