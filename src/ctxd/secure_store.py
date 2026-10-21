import fcntl
import hashlib
import json
import os
import tempfile
import uuid
import math
from contextlib import contextmanager
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
    with _credentials_write_lock():
        payload = _load_credentials()
        credentials = payload.get("ctxfs_api_keys")
        if not isinstance(credentials, dict):
            credentials = {}
        credentials[endpoint] = api_key.strip()
        payload["ctxfs_api_keys"] = credentials
        _save_credentials(payload)


def clear_ctxfs_api_key(*, endpoint: str) -> None:
    with _credentials_write_lock():
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
    with _credentials_write_lock():
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
    return _validate_machine_registration(registration)


def save_ctxfs_machine_registration(
    *, endpoint: str, registration: dict[str, Any]
) -> None:
    if registration.get("state") not in {"pending", "registered"}:
        raise ValueError("machine registration state must be pending or registered")
    with _credentials_write_lock():
        payload = _load_credentials()
        registrations = payload.get("ctxfs_machine_registrations")
        if registrations is None:
            registrations = {}
        if not isinstance(registrations, dict):
            raise CtxdAuthError("Stored ctxd machine registrations are invalid.")
        registrations[endpoint] = registration
        payload["ctxfs_machine_registrations"] = registrations
        _save_credentials(payload)


@contextmanager
def ctxfs_machine_registration_lock(*, endpoint: str):
    """Serialize one endpoint's registration transaction across processes."""
    digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
    lock_path = (
        get_credentials_path().parent / ".ctxd-locks" / f"register-{digest}.lock"
    )
    with _exclusive_file_lock(lock_path):
        yield


@contextmanager
def _credentials_write_lock():
    with _exclusive_file_lock(get_credentials_path().with_suffix(".lock")):
        yield


@contextmanager
def _exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as lock_file:
        try:
            path.chmod(0o600)
        except OSError:
            pass
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _validate_machine_registration(raw: Any) -> dict[str, Any]:
    invalid = CtxdAuthError("Stored ctxd machine registration is invalid.")
    if not isinstance(raw, dict):
        raise invalid
    state = raw.get("state")
    required = {"state", "installation_id", "request"}
    if state == "registered":
        required |= {"machine", "key_id", "token"}
    elif state != "pending":
        raise invalid
    if set(raw) != required:
        raise invalid
    try:
        installation_id = str(uuid.UUID(raw["installation_id"]))
    except (AttributeError, TypeError, ValueError) as exc:
        raise invalid from exc
    if raw["installation_id"] != installation_id:
        raise invalid
    request = raw["request"]
    request_keys = {
        "local_registration_id",
        "name",
        "description",
        "platform",
        "ctxd_version",
    }
    if not isinstance(request, dict) or set(request) != request_keys:
        raise invalid
    try:
        local_id = str(uuid.UUID(request["local_registration_id"]))
        if request["local_registration_id"] != local_id:
            raise ValueError
        _validate_required_text(request["name"], 128)
        _validate_optional_text(request["description"], 1024, allow_empty=True)
        _validate_optional_text(request["platform"], 128)
        _validate_optional_text(request["ctxd_version"], 128)
    except (TypeError, ValueError, AttributeError) as exc:
        raise invalid from exc
    if state == "pending":
        return raw
    machine = raw["machine"]
    machine_keys = {
        "machine_id",
        "machine_key",
        "name",
        "description",
        "state",
        "platform",
        "ctxd_version",
        "credential_key_id",
        "registered_at",
    }
    if not isinstance(machine, dict) or set(machine) != machine_keys:
        raise invalid
    try:
        from ctxd.models import CtxfsMachine

        for field in ("machine_id", "machine_key", "name"):
            _validate_required_text(machine[field], 128)
        _validate_optional_text(machine["description"], 1024, allow_empty=True)
        _validate_optional_text(machine["platform"], 128)
        _validate_optional_text(machine["ctxd_version"], 128)
        _validate_optional_text(machine["credential_key_id"], 128)
        if not isinstance(machine["state"], str):
            raise ValueError
        registered_at = machine["registered_at"]
        if (
            isinstance(registered_at, bool)
            or not isinstance(registered_at, (int, float))
            or not math.isfinite(registered_at)
            or registered_at <= 0
        ):
            raise ValueError
        parsed_machine = CtxfsMachine.model_validate(machine)
        key_id = _validate_required_text(raw["key_id"], 128)
        _validate_required_text(raw["token"], 65536)
    except (TypeError, ValueError) as exc:
        raise invalid from exc
    if parsed_machine.credential_key_id != key_id:
        raise invalid
    return raw


def _validate_required_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError
    return value


def _validate_optional_text(
    value: Any, maximum: int, *, allow_empty: bool = False
) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not allow_empty and not value.strip())
    ):
        raise ValueError
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError


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
