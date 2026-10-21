from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ctxfs.auth import Principal, Scope
from ctxfs.credentials import CredentialStore
from ctxfs.models import Machine
from ctxfs.store import CtxfsStore


@dataclass(frozen=True, slots=True)
class MachineRegistrationRequest:
    local_registration_id: str
    name: str
    description: str | None = None
    platform: str | None = None
    ctxd_version: str | None = None


@dataclass(frozen=True, slots=True)
class MachineRegistrationResult:
    machine: Machine
    key_id: str
    token: str
    recovered: bool


@dataclass(frozen=True, slots=True)
class PreparedMachineCredential:
    key_id: str
    token: str


class MachineCredentialCoordinator:
    def __init__(self, catalog_root: Path, credential_root: Path, user_id: str):
        self.catalog_root = catalog_root
        self.credential_root = credential_root
        self.user_id = user_id

    def register(
        self,
        principal: Principal,
        request: MachineRegistrationRequest,
    ) -> MachineRegistrationResult:
        if Scope.ADMIN not in principal.scopes and Scope.ENROLL not in principal.scopes:
            raise PermissionError("enrollment authority required")
        fingerprint = _request_fingerprint(request)
        with CredentialStore(self.credential_root) as credentials:
            with credentials.immediate_transaction():
                recovered = False
                if Scope.ADMIN not in principal.scopes:
                    assert principal.key_id is not None
                    recovered = credentials.consume_enrollment(
                        principal.key_id,
                        request.local_registration_id.strip(),
                        fingerprint,
                    )
                with _catalog(self.catalog_root) as catalog:
                    machine = catalog.register_machine(
                        self.user_id,
                        local_registration_id=request.local_registration_id,
                        name=request.name,
                        description=request.description,
                        platform=request.platform,
                        ctxd_version=request.ctxd_version,
                    )
                    previous_key = machine.credential_key_id
                    machine_credentials = credentials.machine_credentials(
                        machine.machine_id
                    )
                    for credential in machine_credentials:
                        if credential.key_id != previous_key and credential.active:
                            credentials.revoke_machine_key(
                                credential.key_id, machine.machine_id
                            )
                    issued = credentials.issue_machine(
                        machine.machine_id, machine.machine_key
                    )
                    machine = catalog.bind_machine_credential(
                        self.user_id,
                        machine.machine_id,
                        expected_key_id=previous_key,
                        new_key_id=issued.credential.key_id,
                    )
                    if previous_key is not None and any(
                        credential.key_id == previous_key
                        for credential in machine_credentials
                    ):
                        credentials.revoke_machine_key(
                            previous_key,
                            machine.machine_id,
                            replaced_by=issued.credential.key_id,
                        )
                return MachineRegistrationResult(
                    machine=machine,
                    key_id=issued.credential.key_id,
                    token=issued.token,
                    recovered=recovered,
                )

    def prepare_rotation(
        self, principal: Principal, machine_id: str
    ) -> PreparedMachineCredential:
        with CredentialStore(self.credential_root) as credentials:
            with credentials.immediate_transaction():
                with _catalog(self.catalog_root) as catalog:
                    machine = _require_current(
                        catalog, self.user_id, principal, machine_id
                    )
                    for credential in credentials.machine_credentials(machine_id):
                        if (
                            credential.key_id != machine.credential_key_id
                            and credential.active
                        ):
                            credentials.revoke_machine_key(
                                credential.key_id, machine_id
                            )
                    issued = credentials.issue_machine(
                        machine.machine_id, machine.machine_key
                    )
                    return PreparedMachineCredential(
                        key_id=issued.credential.key_id, token=issued.token
                    )

    def confirm_rotation(
        self,
        principal: Principal,
        machine_id: str,
        replacement_key_id: str,
    ) -> Machine:
        with CredentialStore(self.credential_root) as credentials:
            with credentials.immediate_transaction():
                with _catalog(self.catalog_root) as catalog:
                    machine = _require_current(
                        catalog, self.user_id, principal, machine_id
                    )
                    if machine.credential_key_id == replacement_key_id:
                        return machine
                    replacement = credentials.get(replacement_key_id)
                    _require_machine_credential(replacement, machine)
                    old_key = machine.credential_key_id
                    assert old_key is not None
                    machine = catalog.bind_machine_credential(
                        self.user_id,
                        machine_id,
                        expected_key_id=old_key,
                        new_key_id=replacement_key_id,
                    )
                    credentials.revoke_machine_key(
                        old_key,
                        machine_id,
                        replaced_by=replacement_key_id,
                    )
                    return machine

    def revoke(self, principal: Principal, machine_id: str) -> Machine:
        with CredentialStore(self.credential_root) as credentials:
            with credentials.immediate_transaction():
                with _catalog(self.catalog_root) as catalog:
                    machine = _require_current(
                        catalog, self.user_id, principal, machine_id
                    )
                    key_id = machine.credential_key_id
                    if key_id is None:
                        return machine
                    credentials.revoke_machine_key(key_id, machine_id)
                    return catalog.clear_machine_credential(
                        self.user_id,
                        machine_id,
                        expected_key_id=key_id,
                    )


class _catalog:
    def __init__(self, root: Path):
        self.store = CtxfsStore(root)

    def __enter__(self) -> CtxfsStore:
        return self.store

    def __exit__(self, *_args: object) -> None:
        self.store.close()


def _request_fingerprint(request: MachineRegistrationRequest) -> str:
    payload = {
        "ctxd_version": _optional(request.ctxd_version),
        "description": _optional(request.description),
        "local_registration_id": request.local_registration_id.strip(),
        "name": request.name.strip(),
        "platform": _optional(request.platform),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _optional(value: str | None) -> str | None:
    return None if value is None else value.strip()


def _require_current(
    catalog: CtxfsStore,
    user_id: str,
    principal: Principal,
    machine_id: str,
) -> Machine:
    machine = catalog.machine(user_id, machine_id)
    if Scope.ADMIN in principal.scopes:
        return machine
    if (
        principal.machine_id != machine_id
        or principal.key_id is None
        or machine.credential_key_id != principal.key_id
    ):
        raise PermissionError("machine_credential_not_current")
    return machine


def _require_machine_credential(credential, machine: Machine) -> None:
    expected = frozenset({Scope.SUBMIT, Scope.STATUS_WRITE})
    if (
        not credential.active
        or credential.machine_id != machine.machine_id
        or credential.scopes != expected
        or credential.prefixes != (f"machines/{machine.machine_key}",)
    ):
        raise ValueError("invalid replacement machine credential")
