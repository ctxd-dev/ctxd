from __future__ import annotations

import pytest

from ctxfs import (
    CredentialGrant,
    DenialReason,
    LocalSocketTrust,
    Principal,
    PrincipalKind,
    Scope,
    authorize,
    local_socket_principal,
)


def _grant(*scopes: Scope, prefixes: tuple[str, ...] = ()) -> Principal:
    return CredentialGrant(
        principal_id="client-1",
        key_id="key-1",
        scopes=frozenset(scopes),
        prefixes=prefixes,
    ).principal()


@pytest.mark.parametrize("scope", [Scope.STATUS, Scope.READ])
def test_matching_scope_allows_non_mutating_capability(scope):
    decision = authorize(_grant(scope), scope)

    assert decision.allowed
    assert decision.http_status is None
    assert decision.principal_id == "client-1"
    assert decision.key_id == "key-1"


def test_missing_identity_and_scope_have_distinct_denials():
    unauthenticated = authorize(None, Scope.READ)
    forbidden = authorize(_grant(Scope.STATUS), Scope.READ)

    assert unauthenticated.denial_reason is DenialReason.UNAUTHENTICATED
    assert unauthenticated.http_status == 401
    assert forbidden.denial_reason is DenialReason.MISSING_SCOPE
    assert forbidden.http_status == 403


def test_submit_requires_path_within_server_granted_prefix():
    writer = _grant(
        Scope.SUBMIT,
        prefixes=("teams/alpha", "shared/reference"),
    )

    assert authorize(writer, Scope.SUBMIT, path="teams/alpha").allowed
    assert authorize(writer, Scope.SUBMIT, path="teams/alpha/notes.md").allowed
    assert not authorize(writer, Scope.SUBMIT, path="teams/alphabet/doc.md").allowed
    assert not authorize(writer, Scope.SUBMIT, path="teams/beta/doc.md").allowed
    assert authorize(writer, Scope.SUBMIT).denial_reason is DenialReason.PREFIX_REQUIRED


def test_admin_allows_every_capability_and_prefix():
    admin = _grant(Scope.ADMIN)

    for scope in Scope:
        assert authorize(admin, scope, path="any/prefix/document.md").allowed


def test_local_socket_trust_is_explicit_and_maps_to_admin():
    assert local_socket_principal(LocalSocketTrust.DISABLED) is None

    principal = local_socket_principal(LocalSocketTrust.TRUSTED_ADMIN)
    assert principal is not None
    assert principal.kind is PrincipalKind.LOCAL_SOCKET
    assert authorize(principal, Scope.ADMIN).allowed


def test_unknown_local_socket_policy_cannot_grant_admin():
    with pytest.raises(ValueError, match="LocalSocketTrust"):
        local_socket_principal("unknown")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scopes", "prefixes", "message"),
    [
        (frozenset({Scope.READ}), ("writer/root",), "require the submit scope"),
        (
            frozenset({Scope.ADMIN, Scope.READ}),
            (),
            "must stand alone",
        ),
        (frozenset({Scope.SUBMIT}), ("writer/root", "writer/root"), "unique"),
    ],
)
def test_invalid_grant_combinations_are_rejected(scopes, prefixes, message):
    with pytest.raises(ValueError, match=message):
        CredentialGrant("client-1", "key-1", scopes, prefixes)


def test_request_metadata_cannot_expand_server_derived_grants():
    writer = _grant(Scope.SUBMIT, prefixes=("assigned/root",))

    # No writer ID or caller-claimed prefix enters the policy function.
    decision = authorize(writer, Scope.SUBMIT, path="claimed/root/file.md")

    assert not decision.allowed
    assert decision.denial_reason is DenialReason.PREFIX_NOT_GRANTED


def test_direct_principal_construction_cannot_bypass_grant_invariants():
    with pytest.raises(ValueError, match="require the submit scope"):
        Principal(
            principal_id="client-1",
            key_id="key-1",
            kind=PrincipalKind.API_KEY,
            scopes=frozenset({Scope.READ}),
            prefixes=("writer/root",),
        )

    with pytest.raises(ValueError, match="Scope values"):
        Principal(
            principal_id="client-1",
            key_id="key-1",
            kind=PrincipalKind.API_KEY,
            scopes=frozenset({"read"}),  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="PrincipalKind"):
        Principal(
            principal_id="client-1",
            key_id="key-1",
            kind="api_key",  # type: ignore[arg-type]
            scopes=frozenset({Scope.READ}),
        )


def test_authorize_requires_a_declared_scope_value():
    with pytest.raises(ValueError, match="required_scope"):
        authorize(_grant(Scope.READ), "read")  # type: ignore[arg-type]


@pytest.mark.parametrize("path", ["C:/ctxfs", "safe/line\nbreak", "safe/\x7fbreak"])
def test_authorization_rejects_paths_that_ctxfs_storage_rejects(path):
    writer = _grant(Scope.SUBMIT, prefixes=("safe",))

    with pytest.raises(ValueError, match="normalized relative ctxfs paths"):
        authorize(writer, Scope.SUBMIT, path=path)
