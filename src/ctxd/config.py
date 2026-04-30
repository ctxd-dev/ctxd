import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ctxd.secure_store import (
    clear_secret_bundle,
    load_secret_bundle,
    save_secret_bundle,
)

DEFAULT_BASE_URL = "https://mcp.ctxd.dev"
DEFAULT_CONFIG_PATH = Path.home() / ".ctxd" / "config.json"


def get_config_path() -> Path:
    configured = os.getenv("CTXD_CONFIG_PATH")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CONFIG_PATH


def resolve_api_key(
    api_key: str | None = None, *, base_url: str | None = None
) -> str | None:
    if api_key and api_key.strip():
        return api_key.strip()

    env_api_key = os.getenv("CTXD_API_KEY")
    if env_api_key and env_api_key.strip():
        return env_api_key.strip()

    secret_bundle = load_secret_bundle(
        base_url=resolve_base_url(base_url), client_id=None
    )
    stored_api_key = secret_bundle.get("api_key")
    if isinstance(stored_api_key, str) and stored_api_key.strip():
        return stored_api_key.strip()

    return None


def load_config() -> dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict[str, Any]) -> Path:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f"{path.name}.", delete=False
    ) as tmp:
        tmp.write(json.dumps(config, indent=2, sort_keys=True) + "\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    os.replace(tmp_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def save_api_key(api_key: str, *, base_url: str | None = None) -> Path:
    resolved_base_url = resolve_base_url(base_url)
    config = load_config()
    config["base_url"] = resolved_base_url
    save_secret_bundle(
        {"api_key": api_key.strip()},
        base_url=resolved_base_url,
        client_id=None,
    )
    return save_config(config)


def clear_api_key(*, base_url: str | None = None, keep_base_url: bool = True) -> Path:
    resolved_base_url = resolve_base_url(base_url)
    clear_secret_bundle(base_url=resolved_base_url, client_id=None)

    retained: dict[str, Any] = {}
    if keep_base_url:
        retained["base_url"] = resolved_base_url

    return save_config(retained)


def resolve_base_url(base_url: str | None = None) -> str:
    if base_url and base_url.strip():
        return base_url.strip()

    env_base_url = os.getenv("CTXD_BASE_URL")
    if env_base_url and env_base_url.strip():
        return env_base_url.strip()

    config_base_url = load_config().get("base_url")
    if isinstance(config_base_url, str) and config_base_url.strip():
        return config_base_url.strip()

    return DEFAULT_BASE_URL


def _resolve_base_url_from_config(config: dict[str, Any]) -> str:
    config_base_url = config.get("base_url")
    if isinstance(config_base_url, str) and config_base_url.strip():
        return config_base_url.strip()
    return DEFAULT_BASE_URL
