from __future__ import annotations

import platform as platform_module
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from ctxd._metadata import SDK_VERSION
from ctxd.config import (
    load_config_strict,
    normalize_ctxfs_endpoint,
    resolve_ctxfs_endpoint,
    save_config,
)
from ctxd.ctxfs_client import CtxfsClient
from ctxd.exceptions import CtxdAuthError, CtxdProtocolError
from ctxd.models import CtxfsMachineRegistration
from ctxd.secure_store import (
    load_ctxfs_machine_registration,
    load_installation_proof,
    save_ctxfs_machine_registration,
    save_installation_proof,
)

_UNSET = object()


@dataclass(frozen=True)
class LocalMachineIdentity:
    schema_version: int
    installation_id: str
    local_registration_id: str
    name: str
    description: str | None


@dataclass(frozen=True)
class LocalMachineStatus:
    endpoint: str
    registration_state: str
    installation_proof_matches: bool
    installation_id: str
    local_registration_id: str
    name: str
    description: str | None
    machine_id: str | None
    machine_key: str | None
    credential_key_id: str | None
    metadata_sync: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_or_create_machine_identity() -> LocalMachineIdentity:
    config = load_config_strict()
    raw = config.get("machine")
    proof = load_installation_proof()
    if raw is None:
        identity = _new_identity(_default_name(), None)
        config["machine"] = _identity_dict(identity)
        save_config(config)
        save_installation_proof(identity.installation_id)
        return identity

    identity = _parse_identity(raw)
    if proof == identity.installation_id:
        return identity

    identity = _new_identity(identity.name, identity.description)
    config["machine"] = _identity_dict(identity)
    save_config(config)
    save_installation_proof(identity.installation_id)
    return identity


def configure_machine(
    *, name: str | None = None, description: str | None | object = _UNSET
) -> LocalMachineIdentity:
    if name is None and description is _UNSET:
        raise ValueError("Specify --name, --description, or --clear-description.")
    identity = load_or_create_machine_identity()
    new_name = (
        identity.name if name is None else _validate_text(name, "machine name", 128)
    )
    new_description = (
        identity.description
        if description is _UNSET
        else _validate_description(description)
    )
    updated = LocalMachineIdentity(
        schema_version=identity.schema_version,
        installation_id=identity.installation_id,
        local_registration_id=identity.local_registration_id,
        name=new_name,
        description=new_description,
    )
    config = load_config_strict()
    config["machine"] = _identity_dict(updated)
    save_config(config)
    return updated


def register_machine(
    *, endpoint: str | None = None, enrollment_token: str | None = None
) -> tuple[LocalMachineStatus, bool]:
    identity = load_or_create_machine_identity()
    resolved_endpoint = normalize_ctxfs_endpoint(endpoint or resolve_ctxfs_endpoint())
    stored = load_ctxfs_machine_registration(endpoint=resolved_endpoint)
    request = _registration_request(identity)
    if stored is not None and stored.get("installation_id") == identity.installation_id:
        request = _parse_stored_request(stored.get("request"))
    else:
        save_ctxfs_machine_registration(
            endpoint=resolved_endpoint,
            registration={
                "state": "pending",
                "installation_id": identity.installation_id,
                "request": request,
            },
        )

    result = CtxfsClient(endpoint=resolved_endpoint, timeout=30.0).register_machine(
        **request, enrollment_token=enrollment_token
    )
    # Revalidate at the persistence boundary as callers and test doubles can return
    # constructed or subsequently mutated model instances.
    result = CtxfsMachineRegistration.model_validate(result.model_dump(mode="python"))
    if result.machine.credential_key_id != result.key_id:
        raise CtxdProtocolError(
            "ctxfs registration response credential binding did not match"
        )
    save_ctxfs_machine_registration(
        endpoint=resolved_endpoint,
        registration={
            "state": "registered",
            "installation_id": identity.installation_id,
            "request": request,
            "machine": result.machine.model_dump(mode="json"),
            "key_id": result.key_id,
            "token": result.token,
        },
    )
    return machine_status(endpoint=resolved_endpoint), result.recovered


def machine_status(*, endpoint: str | None = None) -> LocalMachineStatus:
    identity = load_or_create_machine_identity()
    resolved_endpoint = normalize_ctxfs_endpoint(endpoint or resolve_ctxfs_endpoint())
    proof_matches = load_installation_proof() == identity.installation_id
    stored = load_ctxfs_machine_registration(endpoint=resolved_endpoint)
    if stored is None or stored.get("installation_id") != identity.installation_id:
        return _status(identity, resolved_endpoint, "unregistered", proof_matches)

    request = _parse_stored_request(stored.get("request"))
    metadata_sync = (
        "current"
        if request["name"] == identity.name
        and request["description"] == identity.description
        else "pending_server_update"
    )
    if stored["state"] == "pending":
        return _status(
            identity,
            resolved_endpoint,
            "registration_pending",
            proof_matches,
            metadata_sync=metadata_sync,
        )
    machine = stored.get("machine")
    if (
        not isinstance(machine, dict)
        or not isinstance(machine.get("machine_id"), str)
        or not isinstance(machine.get("machine_key"), str)
        or not isinstance(stored.get("key_id"), str)
        or not isinstance(stored.get("token"), str)
    ):
        raise CtxdAuthError("Stored ctxd machine registration is invalid.")
    return _status(
        identity,
        resolved_endpoint,
        "registered",
        proof_matches,
        machine_id=machine["machine_id"],
        machine_key=machine["machine_key"],
        credential_key_id=stored["key_id"],
        metadata_sync=metadata_sync,
    )


def unchanged_description() -> object:
    return _UNSET


def _new_identity(name: str, description: str | None) -> LocalMachineIdentity:
    return LocalMachineIdentity(
        schema_version=1,
        installation_id=str(uuid.uuid4()),
        local_registration_id=str(uuid.uuid4()),
        name=_validate_text(name, "machine name", 128),
        description=_validate_description(description),
    )


def _parse_identity(raw: Any) -> LocalMachineIdentity:
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Stored machine configuration is invalid.")
    try:
        installation_id = str(uuid.UUID(raw["installation_id"]))
        local_registration_id = str(uuid.UUID(raw["local_registration_id"]))
        name = _validate_text(raw["name"], "machine name", 128)
        description = _validate_description(raw.get("description"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Stored machine configuration is invalid.") from exc
    return LocalMachineIdentity(
        1, installation_id, local_registration_id, name, description
    )


def _identity_dict(identity: LocalMachineIdentity) -> dict[str, Any]:
    return asdict(identity)


def _registration_request(identity: LocalMachineIdentity) -> dict[str, Any]:
    platform_value = platform_module.platform().strip() or None
    return {
        "local_registration_id": identity.local_registration_id,
        "name": identity.name,
        "description": identity.description,
        "platform": platform_value,
        "ctxd_version": SDK_VERSION,
    }


def _parse_stored_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {
        "local_registration_id",
        "name",
        "description",
        "platform",
        "ctxd_version",
    }:
        raise CtxdAuthError("Stored ctxd machine registration is invalid.")
    try:
        return {
            "local_registration_id": _validate_text(
                raw["local_registration_id"], "local registration ID", 128
            ),
            "name": _validate_text(raw["name"], "machine name", 128),
            "description": _validate_description(raw["description"]),
            "platform": _validate_optional_text(raw["platform"], "platform", 128),
            "ctxd_version": _validate_optional_text(
                raw["ctxd_version"], "ctxd version", 128
            ),
        }
    except (TypeError, ValueError) as exc:
        raise CtxdAuthError("Stored ctxd machine registration is invalid.") from exc


def _status(
    identity: LocalMachineIdentity,
    endpoint: str,
    state: str,
    proof_matches: bool,
    *,
    machine_id: str | None = None,
    machine_key: str | None = None,
    credential_key_id: str | None = None,
    metadata_sync: str = "current",
) -> LocalMachineStatus:
    return LocalMachineStatus(
        endpoint=endpoint,
        registration_state=state,
        installation_proof_matches=proof_matches,
        installation_id=identity.installation_id,
        local_registration_id=identity.local_registration_id,
        name=identity.name,
        description=identity.description,
        machine_id=machine_id,
        machine_key=machine_key,
        credential_key_id=credential_key_id,
        metadata_sync=metadata_sync,
    )


def _validate_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")
    clean = value.strip()
    if len(clean) > maximum or _has_control(clean):
        raise ValueError(f"{label} is invalid")
    return clean


def _validate_optional_text(value: Any, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _validate_text(value, label, maximum)


def _validate_description(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 1024 or _has_control(value):
        raise ValueError("machine description is invalid")
    return value


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _default_name() -> str:
    return platform_module.node().strip() or "This machine"
