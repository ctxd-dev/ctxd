"""Pure authorization policy for the single-tenant ctxfs service.

This module deliberately does not parse or store credentials.  Authentication
adapters construct :class:`Principal` instances from server-owned credential
records and pass them to :func:`authorize`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

_ASCII_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Scope(str, Enum):
    STATUS = "status"
    STATUS_WRITE = "status:write"
    READ = "read"
    SUBMIT = "submit"
    ENROLL = "enroll"
    ADMIN = "admin"


class PrincipalKind(str, Enum):
    API_KEY = "api_key"
    LOCAL_SOCKET = "local_socket"


class LocalSocketTrust(str, Enum):
    """Whether an authenticated local Unix-socket peer is fully trusted."""

    DISABLED = "disabled"
    TRUSTED_ADMIN = "trusted_admin"


class DenialReason(str, Enum):
    UNAUTHENTICATED = "unauthenticated"
    MISSING_SCOPE = "missing_scope"
    PREFIX_REQUIRED = "prefix_required"
    PREFIX_NOT_GRANTED = "prefix_not_granted"


@dataclass(frozen=True, slots=True)
class CredentialGrant:
    """Server-owned authorization data associated with one API key."""

    principal_id: str
    key_id: str
    scopes: frozenset[Scope]
    prefixes: tuple[str, ...] = ()
    machine_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.principal_id, "principal_id")
        _require_identifier(self.key_id, "key_id")
        if self.machine_id is not None:
            _require_identifier(self.machine_id, "machine_id")
        normalized = _validate_grants(self.scopes, self.prefixes, self.machine_id)
        object.__setattr__(self, "prefixes", normalized)

    def principal(self) -> Principal:
        return Principal(
            principal_id=self.principal_id,
            kind=PrincipalKind.API_KEY,
            key_id=self.key_id,
            scopes=self.scopes,
            prefixes=self.prefixes,
            machine_id=self.machine_id,
        )


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated server-derived identity used for authorization decisions."""

    principal_id: str
    kind: PrincipalKind
    scopes: frozenset[Scope]
    key_id: str | None = None
    prefixes: tuple[str, ...] = ()
    machine_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.principal_id, "principal_id")
        if not isinstance(self.kind, PrincipalKind):
            raise ValueError("kind must be a PrincipalKind value")
        if self.kind is PrincipalKind.API_KEY and not self.key_id:
            raise ValueError("API-key principals require a key_id")
        if self.kind is PrincipalKind.LOCAL_SOCKET and self.key_id is not None:
            raise ValueError("local-socket principals cannot have a key_id")
        if self.key_id is not None:
            _require_identifier(self.key_id, "key_id")
        if self.machine_id is not None:
            _require_identifier(self.machine_id, "machine_id")
        normalized = _validate_grants(self.scopes, self.prefixes, self.machine_id)
        object.__setattr__(self, "prefixes", normalized)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    required_scope: Scope
    principal_id: str | None
    key_id: str | None
    denial_reason: DenialReason | None = None

    @property
    def http_status(self) -> int | None:
        """Return a denial response status, or ``None`` when routing may continue."""

        if self.allowed:
            return None
        if self.denial_reason is DenialReason.UNAUTHENTICATED:
            return 401
        return 403


def local_socket_principal(policy: LocalSocketTrust) -> Principal | None:
    """Return the trusted local principal selected by an explicit server policy."""

    if not isinstance(policy, LocalSocketTrust):
        raise ValueError("policy must be a LocalSocketTrust value")
    if policy is LocalSocketTrust.DISABLED:
        return None
    return Principal(
        principal_id="local-socket",
        kind=PrincipalKind.LOCAL_SOCKET,
        scopes=frozenset({Scope.ADMIN}),
    )


def authorize(
    principal: Principal | None,
    required_scope: Scope,
    *,
    path: str | None = None,
) -> AuthorizationDecision:
    """Authorize a capability, applying prefix grants to submissions.

    ``path`` is required for submit decisions.  The caller must obtain the
    principal and grants from server-owned authentication state; request fields
    such as ``writer_id`` and ``writer_prefix`` are intentionally absent.
    """

    if not isinstance(required_scope, Scope):
        raise ValueError("required_scope must be a Scope value")
    if principal is None:
        return _denied(required_scope, DenialReason.UNAUTHENTICATED)
    if Scope.ADMIN in principal.scopes:
        return _allowed(principal, required_scope)
    if required_scope not in principal.scopes:
        return _denied(required_scope, DenialReason.MISSING_SCOPE, principal)
    if required_scope is not Scope.SUBMIT:
        return _allowed(principal, required_scope)
    if path is None:
        return _denied(required_scope, DenialReason.PREFIX_REQUIRED, principal)
    normalized_path = _normalize_path(path)
    if not any(_within(normalized_path, prefix) for prefix in principal.prefixes):
        return _denied(required_scope, DenialReason.PREFIX_NOT_GRANTED, principal)
    return _allowed(principal, required_scope)


def _allowed(principal: Principal, scope: Scope) -> AuthorizationDecision:
    return AuthorizationDecision(True, scope, principal.principal_id, principal.key_id)


def _denied(
    scope: Scope,
    reason: DenialReason,
    principal: Principal | None = None,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        False,
        scope,
        principal.principal_id if principal else None,
        principal.key_id if principal else None,
        reason,
    )


def _require_identifier(value: str, field: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{field} must be a non-empty, trimmed string")


def _validate_grants(
    scopes: frozenset[Scope], prefixes: tuple[str, ...], machine_id: str | None = None
) -> tuple[str, ...]:
    if any(not isinstance(scope, Scope) for scope in scopes):
        raise ValueError("scopes must contain Scope values")
    normalized = tuple(_normalize_prefix(prefix) for prefix in prefixes)
    if len(set(normalized)) != len(normalized):
        raise ValueError("prefix grants must be unique")
    if normalized and Scope.SUBMIT not in scopes:
        raise ValueError("prefix grants require the submit scope")
    if Scope.ADMIN in scopes and len(scopes) != 1:
        raise ValueError("admin is an all-capability scope and must stand alone")
    if Scope.ENROLL in scopes and len(scopes) != 1:
        raise ValueError("enroll scope must stand alone")
    if Scope.ENROLL in scopes and normalized:
        raise ValueError("enroll scope cannot have prefix grants")
    if Scope.STATUS_WRITE in scopes and machine_id is None:
        raise ValueError("status:write scope requires a machine binding")
    if machine_id is not None and (
        scopes != frozenset({Scope.SUBMIT, Scope.STATUS_WRITE}) or len(normalized) != 1
    ):
        raise ValueError(
            "machine credentials require submit and status:write with one prefix"
        )
    return normalized


def _normalize_prefix(prefix: str) -> str:
    normalized = _normalize_path(prefix)
    if not normalized:
        raise ValueError("prefix grants cannot be empty")
    return normalized


def _normalize_path(path: str) -> str:
    if (
        not path
        or path.startswith("/")
        or path.endswith("/")
        or _has_control_character(path)
        or path.startswith(tuple(f"{letter}:" for letter in _ASCII_LETTERS))
    ):
        raise ValueError("paths must be normalized relative ctxfs paths")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("paths must be normalized relative paths")
    return "/".join(parts)


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")
