from __future__ import annotations

import pytest

from ctxfs import (
    CtxfsStore,
    MachineCredentialCoordinator,
    MachineRegistrationRequest,
    Scope,
)
from ctxfs.credentials import CredentialStore, EnrollmentConsumedError


def _principal(issued):
    with CredentialStore(issued[0]) as store:
        grant = store.verify(issued[1])
    assert grant is not None
    return grant.principal()


def _enrollment(root):
    with CredentialStore(root) as store:
        issued = store.create(name="enroll", scopes=[Scope.ENROLL])
    return root, issued.token


def test_registration_binds_exact_machine_authority_and_recovers_retry(tmp_path):
    root = tmp_path / "ctxfs"
    enrollment = _enrollment(root)
    principal = _principal(enrollment)
    coordinator = MachineCredentialCoordinator(root, root, "local")
    request = MachineRegistrationRequest("local-1", "Alice's Mac")

    first = coordinator.register(principal, request)
    second = coordinator.register(principal, request)

    assert not first.recovered
    assert second.recovered
    assert first.machine.machine_id == second.machine.machine_id
    assert first.key_id != second.key_id
    with CredentialStore(root) as credentials:
        assert credentials.verify(first.token) is None
        replacement = credentials.verify(second.token)
        assert replacement is not None
        assert replacement.machine_id == second.machine.machine_id
        assert replacement.scopes == frozenset({Scope.SUBMIT, Scope.STATUS_WRITE})
        assert replacement.prefixes == (f"machines/{second.machine.machine_key}",)
    catalog = CtxfsStore(root)
    try:
        assert (
            catalog.machine("local", second.machine.machine_id).credential_key_id
            == second.key_id
        )
    finally:
        catalog.close()


def test_enrollment_rejects_different_request_and_other_machine_is_unaffected(tmp_path):
    root = tmp_path / "ctxfs"
    principal = _principal(_enrollment(root))
    coordinator = MachineCredentialCoordinator(root, root, "local")
    first = coordinator.register(
        principal, MachineRegistrationRequest("local-1", "Laptop")
    )

    with pytest.raises(EnrollmentConsumedError):
        coordinator.register(principal, MachineRegistrationRequest("local-2", "Other"))
    with CredentialStore(root) as credentials:
        assert credentials.verify(first.token) is not None


def test_registration_retry_recovers_catalog_bind_after_credential_rollback(
    tmp_path, monkeypatch
):
    root = tmp_path / "ctxfs"
    principal = _principal(_enrollment(root))
    coordinator = MachineCredentialCoordinator(root, root, "local")
    request = MachineRegistrationRequest("local-1", "Laptop")
    original_bind = CtxfsStore.bind_machine_credential
    failed = False

    def bind_then_fail(self, *args, **kwargs):
        nonlocal failed
        result = original_bind(self, *args, **kwargs)
        if not failed:
            failed = True
            raise RuntimeError("injected failure after catalog bind")
        return result

    monkeypatch.setattr(CtxfsStore, "bind_machine_credential", bind_then_fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        coordinator.register(principal, request)
    monkeypatch.setattr(CtxfsStore, "bind_machine_credential", original_bind)

    recovered = coordinator.register(principal, request)
    with CredentialStore(root) as credentials:
        active = [
            item
            for item in credentials.machine_credentials(recovered.machine.machine_id)
            if item.active
        ]
        assert [item.key_id for item in active] == [recovered.key_id]
        assert credentials.verify(recovered.token) is not None


def test_prepare_confirm_rotation_and_revoke(tmp_path):
    root = tmp_path / "ctxfs"
    coordinator = MachineCredentialCoordinator(root, root, "local")
    registered = coordinator.register(
        _principal(_enrollment(root)),
        MachineRegistrationRequest("local-1", "Laptop"),
    )
    with CredentialStore(root) as credentials:
        current = credentials.verify(registered.token)
    assert current is not None
    principal = current.principal()

    prepared = coordinator.prepare_rotation(principal, registered.machine.machine_id)
    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is not None
        assert credentials.verify(prepared.token) is not None
    catalog = CtxfsStore(root)
    try:
        assert (
            catalog.machine("local", registered.machine.machine_id).credential_key_id
            == registered.key_id
        )
    finally:
        catalog.close()

    rotated = coordinator.confirm_rotation(
        principal, registered.machine.machine_id, prepared.key_id
    )
    assert rotated.credential_key_id == prepared.key_id
    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is None
        replacement = credentials.verify(prepared.token)
    assert replacement is not None

    revoked = coordinator.revoke(replacement.principal(), registered.machine.machine_id)
    assert revoked.credential_key_id is None
    with CredentialStore(root) as credentials:
        assert credentials.verify(prepared.token) is None
