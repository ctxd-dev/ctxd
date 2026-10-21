from __future__ import annotations

import pytest

import ctxfs.credentials as credentials_module
from ctxfs import (
    CredentialGrant,
    CtxfsStore,
    MachineCredentialCoordinator,
    MachineRegistrationRequest,
    Scope,
)
from ctxfs.credentials import CredentialStore, EnrollmentConsumedError
from ctxfs.store import CatalogConflictError


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


def test_different_enrollment_cannot_claim_existing_registration(tmp_path):
    root = tmp_path / "ctxfs"
    coordinator = MachineCredentialCoordinator(root, root, "local")
    request = MachineRegistrationRequest("local-1", "Laptop")
    first_enrollment = _enrollment(root)
    registered = coordinator.register(_principal(first_enrollment), request)

    with pytest.raises(CatalogConflictError):
        coordinator.register(_principal(_enrollment(root)), request)

    with CredentialStore(root) as credentials:
        assert credentials.verify(first_enrollment[1]) is not None
        assert credentials.verify(registered.token) is not None
        active = [
            item.key_id
            for item in credentials.machine_credentials(registered.machine.machine_id)
            if item.active
        ]
    assert active == [registered.key_id]
    catalog = CtxfsStore(root)
    try:
        assert (
            catalog.machine("local", registered.machine.machine_id).credential_key_id
            == registered.key_id
        )
    finally:
        catalog.close()


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
    assert recovered.recovered
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

    with pytest.raises(ValueError, match="invalid rotation confirmation retry"):
        coordinator.confirm_rotation(
            principal, registered.machine.machine_id, registered.key_id
        )
    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is not None
        assert credentials.verify(prepared.token) is not None

    rotated = coordinator.confirm_rotation(
        principal, registered.machine.machine_id, prepared.key_id
    )
    assert rotated.credential_key_id == prepared.key_id
    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is None
        replacement = credentials.verify(prepared.token)
    assert replacement is not None
    assert (
        coordinator.confirm_rotation(
            replacement.principal(),
            registered.machine.machine_id,
            prepared.key_id,
        ).credential_key_id
        == prepared.key_id
    )

    revoked = coordinator.revoke(replacement.principal(), registered.machine.machine_id)
    assert revoked.credential_key_id is None
    admin = CredentialGrant(
        principal_id="admin-key",
        key_id="admin-key",
        scopes=frozenset({Scope.ADMIN}),
    ).principal()
    assert (
        coordinator.revoke(admin, registered.machine.machine_id).credential_key_id
        is None
    )
    with CredentialStore(root) as credentials:
        assert credentials.verify(prepared.token) is None


def test_confirm_rotation_retry_reconciles_credential_rollback(tmp_path, monkeypatch):
    root = tmp_path / "ctxfs"
    coordinator = MachineCredentialCoordinator(root, root, "local")
    registered = coordinator.register(
        _principal(_enrollment(root)),
        MachineRegistrationRequest("local-1", "Laptop"),
    )
    with CredentialStore(root) as credentials:
        current = credentials.verify(registered.token)
    assert current is not None
    prepared = coordinator.prepare_rotation(
        current.principal(), registered.machine.machine_id
    )
    original_bind = CtxfsStore.bind_machine_credential

    def bind_then_fail(self, *args, **kwargs):
        original_bind(self, *args, **kwargs)
        raise RuntimeError("injected failure after catalog bind")

    monkeypatch.setattr(CtxfsStore, "bind_machine_credential", bind_then_fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        coordinator.confirm_rotation(
            current.principal(), registered.machine.machine_id, prepared.key_id
        )
    monkeypatch.setattr(CtxfsStore, "bind_machine_credential", original_bind)

    with CredentialStore(root) as credentials:
        replacement = credentials.verify(prepared.token)
        assert credentials.verify(registered.token) is not None
    assert replacement is not None
    coordinator.confirm_rotation(
        replacement.principal(), registered.machine.machine_id, prepared.key_id
    )
    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is None
        assert credentials.verify(prepared.token) is not None


def test_confirm_rotation_retry_with_frozen_clock_revokes_exact_predecessor(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(credentials_module.time, "time", lambda: 1_800_000_000.0)
    root = tmp_path / "ctxfs"
    coordinator = MachineCredentialCoordinator(root, root, "local")
    registered = coordinator.register(
        _principal(_enrollment(root)),
        MachineRegistrationRequest("local-1", "Laptop"),
    )
    with CredentialStore(root) as credentials:
        current = credentials.verify(registered.token)
    assert current is not None
    prepared = coordinator.prepare_rotation(
        current.principal(), registered.machine.machine_id
    )
    original_bind = CtxfsStore.bind_machine_credential

    def bind_then_fail(self, *args, **kwargs):
        original_bind(self, *args, **kwargs)
        raise RuntimeError("injected failure after catalog bind")

    monkeypatch.setattr(CtxfsStore, "bind_machine_credential", bind_then_fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        coordinator.confirm_rotation(
            current.principal(), registered.machine.machine_id, prepared.key_id
        )
    monkeypatch.setattr(CtxfsStore, "bind_machine_credential", original_bind)

    with CredentialStore(root) as credentials:
        replacement = credentials.verify(prepared.token)
        old = credentials.get(registered.key_id)
        new = credentials.get(prepared.key_id)
        assert old.created_at == new.created_at
    assert replacement is not None
    coordinator.confirm_rotation(
        replacement.principal(), registered.machine.machine_id, prepared.key_id
    )
    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is None
        assert credentials.get(registered.key_id).replaced_by == prepared.key_id
        assert credentials.verify(prepared.token) is not None


def test_admin_revoke_retry_reconciles_credential_rollback(tmp_path, monkeypatch):
    root = tmp_path / "ctxfs"
    coordinator = MachineCredentialCoordinator(root, root, "local")
    registered = coordinator.register(
        _principal(_enrollment(root)),
        MachineRegistrationRequest("local-1", "Laptop"),
    )
    with CredentialStore(root) as credentials:
        current = credentials.verify(registered.token)
    assert current is not None
    original_clear = CtxfsStore.clear_machine_credential

    def clear_then_fail(self, *args, **kwargs):
        original_clear(self, *args, **kwargs)
        raise RuntimeError("injected failure after catalog clear")

    monkeypatch.setattr(CtxfsStore, "clear_machine_credential", clear_then_fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        coordinator.revoke(current.principal(), registered.machine.machine_id)
    monkeypatch.setattr(CtxfsStore, "clear_machine_credential", original_clear)

    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is not None
    admin = CredentialGrant(
        principal_id="admin-key",
        key_id="admin-key",
        scopes=frozenset({Scope.ADMIN}),
    ).principal()
    assert (
        coordinator.revoke(admin, registered.machine.machine_id).credential_key_id
        is None
    )
    with CredentialStore(root) as credentials:
        assert credentials.verify(registered.token) is None
