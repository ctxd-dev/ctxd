import json
import os
import tempfile
import uuid
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


def load_installation_proof() -> str | None:
    payload = _load_credentials()
    proof = payload.get("installation_proof")
    if proof is None:
        return None
    if not isinstance(proof, str):
        raise CtxdAuthError("Stored ctxd installation proof is invalid.")
    try:
        return str(uuid.UUID(proof))
    except ValueError as exc:
        raise CtxdAuthError("Stored ctxd installation proof is invalid.") from exc


def save_installation_proof(installation_id: str) -> None:
    try:
        installation_id = str(uuid.UUID(installation_id))
    except ValueError as exc:
        raise ValueError("installation ID is invalid") from exc
    payload = _load_credentials()
    payload["installation_proof"] = installation_id
    _save_credentials(payload)


def load_ctxfs_machine_registration(*, endpoint: str) -> dict[str, Any] | None:
    payload = _load_credentials()
    registrations = payload.get("ctxfs_machine_registrations", {})
    if not isinstance(registrations, dict):
        raise CtxdAuthError("Stored ctxd machine registrations are invalid.")
    registration = registrations.get(endpoint)
    if registration is None:
        return None
    if not isinstance(registration, dict) or registration.get("state") not in {
        "pending",
        "registered",
    }:
        raise CtxdAuthError("Stored ctxd machine registration is invalid.")
    return registration


def save_ctxfs_machine_registration(
    *, endpoint: str, registration: dict[str, Any]
) -> None:
    if registration.get("state") not in {"pending", "registered"}:
        raise ValueError("machine registration state must be pending or registered")
    payload = _load_credentials()
    registrations = payload.get("ctxfs_machine_registrations")
    if registrations is None:
        registrations = {}
    if not isinstance(registrations, dict):
        raise CtxdAuthError("Stored ctxd machine registrations are invalid.")
    registrations[endpoint] = registration
    payload["ctxfs_machine_registrations"] = registrations
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
