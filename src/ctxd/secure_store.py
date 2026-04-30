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


def load_secret_bundle(*, base_url: str, client_id: str | None) -> dict[str, Any]:
    del base_url, client_id
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


def save_secret_bundle(
    bundle: dict[str, Any], *, base_url: str, client_id: str | None
) -> None:
    del base_url, client_id
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


def clear_secret_bundle(*, base_url: str, client_id: str | None) -> None:
    del base_url, client_id
    path = get_credentials_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CtxdAuthError("Unable to remove stored ctxd credentials.") from exc
