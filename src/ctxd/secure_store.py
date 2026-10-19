import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ctxd.exceptions import CtxdAuthError

DEFAULT_CREDENTIALS_PATH = Path.home() / ".ctxd" / "credentials.json"


def get_credentials_path() -> Path:
    configured = os.getenv("CTXD_CREDENTIALS_PATH")
    if configured:
        return Path(configured).expanduser()

    config_path = os.getenv("CTXD_CONFIG_PATH")
    if config_path:
        return Path(config_path).expanduser().parent / "credentials.json"

    return DEFAULT_CREDENTIALS_PATH


def _load_credentials() -> dict[str, Any]:
    path = get_credentials_path()
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CtxdAuthError("Stored ctxd credentials are invalid.") from exc

    if not isinstance(payload, dict):
        raise CtxdAuthError("Stored ctxd credentials are invalid.")
    return payload


def load_ctxfs_api_key(*, endpoint: str) -> str | None:
    payload = _load_credentials()
    credentials = payload.get("ctxfs_api_keys", {})
    if not isinstance(credentials, dict):
        return None
    value = credentials.get(endpoint)
    return value.strip() if isinstance(value, str) and value.strip() else None


def save_ctxfs_api_key(*, endpoint: str, api_key: str) -> None:
    payload = _load_credentials()
    credentials = payload.get("ctxfs_api_keys")
    if not isinstance(credentials, dict):
        credentials = {}
    credentials[endpoint] = api_key.strip()
    payload["ctxfs_api_keys"] = credentials
    _save_credentials(payload)


def clear_ctxfs_api_key(*, endpoint: str) -> None:
    payload = _load_credentials()
    credentials = payload.get("ctxfs_api_keys")
    if not isinstance(credentials, dict) or endpoint not in credentials:
        return
    del credentials[endpoint]
    if credentials:
        payload["ctxfs_api_keys"] = credentials
    else:
        payload.pop("ctxfs_api_keys", None)
    _save_credentials(payload)


def _save_credentials(bundle: dict[str, Any]) -> None:
    path = get_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f"{path.name}.", delete=False
    ) as tmp:
        tmp.write(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    os.replace(tmp_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
