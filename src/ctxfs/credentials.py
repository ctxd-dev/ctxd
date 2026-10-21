"""Server-owned ctxfs API-key persistence and lifecycle operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ctxfs.auth import CredentialGrant, Scope

TOKEN_PREFIX = "ctxfs"
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16}$")
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_NAME_LENGTH = 128
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


@dataclass(frozen=True, slots=True)
class Credential:
    key_id: str
    name: str
    scopes: frozenset[Scope]
    prefixes: tuple[str, ...]
    created_at: float
    expires_at: float | None
    revoked_at: float | None
    replaced_by: str | None
    machine_id: str | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > time.time()
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "name": self.name,
            "scopes": sorted(scope.value for scope in self.scopes),
            "prefixes": list(self.prefixes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "replaced_by": self.replaced_by,
            "machine_id": self.machine_id,
            "active": self.active,
        }


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    credential: Credential
    token: str


class EnrollmentConsumedError(ValueError):
    pass


class MachineCredentialLifecycleError(ValueError):
    pass


class CredentialStore:
    """SQLite credential store rooted in private server-owned state."""

    def __init__(self, root: Path):
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / "credentials.sqlite3"
        self._secure_database_file()
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self._migrate()
        os.chmod(self.path, 0o600)

    def _secure_database_file(self) -> None:
        """Create/open the database without an initial world-readable window."""

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("credential database must be a regular file")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def __enter__(self) -> CredentialStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS credentials (
                    key_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    prefixes TEXT NOT NULL,
                    salt BLOB NOT NULL,
                    verifier BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    revoked_at REAL,
                    replaced_by TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in self.connection.execute(
                    "PRAGMA table_info(credentials)"
                ).fetchall()
            }
            if "machine_id" not in columns:
                self.connection.execute(
                    "ALTER TABLE credentials ADD COLUMN machine_id TEXT"
                )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS enrollment_uses (
                    enrollment_key_id TEXT PRIMARY KEY,
                    local_registration_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    machine_id TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            enrollment_columns = {
                row["name"]
                for row in self.connection.execute(
                    "PRAGMA table_info(enrollment_uses)"
                ).fetchall()
            }
            if "machine_id" not in enrollment_columns:
                self.connection.execute(
                    "ALTER TABLE enrollment_uses ADD COLUMN machine_id TEXT"
                )

    @contextmanager
    def immediate_transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def create(
        self,
        *,
        name: str,
        scopes: Iterable[Scope | str],
        prefixes: Iterable[str] = (),
        expires_at: float | None = None,
    ) -> IssuedCredential:
        issued, salt, verifier = self._prepare(
            name=name, scopes=scopes, prefixes=prefixes, expires_at=expires_at
        )
        with self.connection:
            self._insert(issued.credential, salt, verifier)
        return issued

    def _prepare(
        self,
        *,
        name: str,
        scopes: Iterable[Scope | str],
        prefixes: Iterable[str] = (),
        expires_at: float | None = None,
        machine_id: str | None = None,
    ) -> tuple[IssuedCredential, bytes, bytes]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("credential name must be non-empty")
        if len(clean_name) > MAX_NAME_LENGTH:
            raise ValueError(
                f"credential name must be at most {MAX_NAME_LENGTH} characters"
            )
        if any(
            ord(character) < 32 or ord(character) == 127 for character in clean_name
        ):
            raise ValueError("credential name cannot contain control characters")
        normalized_scopes = frozenset(
            scope if isinstance(scope, Scope) else Scope(scope) for scope in scopes
        )
        if not normalized_scopes:
            raise ValueError("credential must have at least one scope")
        key_id = secrets.token_urlsafe(12)
        while key_id.startswith("-"):
            key_id = secrets.token_urlsafe(12)
        # 32 random bytes provides 256 bits of secret material.
        secret = secrets.token_urlsafe(32)
        token = f"{TOKEN_PREFIX}.{key_id}.{secret}"
        grant = CredentialGrant(
            principal_id=key_id,
            key_id=key_id,
            scopes=normalized_scopes,
            prefixes=tuple(prefixes),
            machine_id=machine_id,
        )
        now = time.time()
        if expires_at is not None and (
            not math.isfinite(expires_at) or expires_at <= now
        ):
            raise ValueError("credential expiry must be a finite time in the future")
        salt = secrets.token_bytes(16)
        verifier = _derive(token, salt)
        credential = Credential(
            key_id=key_id,
            name=clean_name,
            scopes=grant.scopes,
            prefixes=grant.prefixes,
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
            replaced_by=None,
            machine_id=machine_id,
        )
        return IssuedCredential(credential, token), salt, verifier

    def _insert(self, credential: Credential, salt: bytes, verifier: bytes) -> None:
        self.connection.execute(
            """
            INSERT INTO credentials (
                key_id, name, scopes, prefixes, salt, verifier,
                created_at, expires_at, revoked_at, replaced_by, machine_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                credential.key_id,
                credential.name,
                json.dumps(sorted(scope.value for scope in credential.scopes)),
                json.dumps(credential.prefixes),
                salt,
                verifier,
                credential.created_at,
                credential.expires_at,
                credential.machine_id,
            ),
        )

    def list(self) -> list[Credential]:
        rows = self.connection.execute(
            "SELECT * FROM credentials ORDER BY created_at, key_id"
        ).fetchall()
        return [_credential(row) for row in rows]

    def get(self, key_id: str) -> Credential:
        _validate_key_id(key_id)
        row = self.connection.execute(
            "SELECT * FROM credentials WHERE key_id = ?", (key_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown credential: {key_id}")
        return _credential(row)

    def verify(self, token: str, *, now: float | None = None) -> CredentialGrant | None:
        parsed = _parse_token(token)
        if parsed is None:
            return None
        key_id = parsed
        row = self.connection.execute(
            "SELECT * FROM credentials WHERE key_id = ?", (key_id,)
        ).fetchone()
        if row is None or not hmac.compare_digest(
            _derive(token, row["salt"]), row["verifier"]
        ):
            return None
        current = time.time() if now is None else now
        if row["revoked_at"] is not None or (
            row["expires_at"] is not None and row["expires_at"] <= current
        ):
            return None
        credential = _credential(row)
        return CredentialGrant(
            principal_id=credential.key_id,
            key_id=credential.key_id,
            scopes=credential.scopes,
            prefixes=credential.prefixes,
            machine_id=credential.machine_id,
        )

    def consume_enrollment(
        self,
        key_id: str,
        local_registration_id: str,
        request_fingerprint: str,
    ) -> bool:
        row = self.connection.execute(
            "SELECT * FROM enrollment_uses WHERE enrollment_key_id = ?",
            (key_id,),
        ).fetchone()
        if row is None:
            self.connection.execute(
                """
                INSERT INTO enrollment_uses(
                    enrollment_key_id, local_registration_id,
                    request_fingerprint, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (key_id, local_registration_id, request_fingerprint, time.time()),
            )
            return False
        if (
            row["local_registration_id"] != local_registration_id
            or row["request_fingerprint"] != request_fingerprint
        ):
            raise EnrollmentConsumedError("enrollment credential already consumed")
        return True

    def bind_enrollment_use(self, key_id: str, machine_id: str) -> None:
        cursor = self.connection.execute(
            """
            UPDATE enrollment_uses
            SET machine_id = COALESCE(machine_id, ?)
            WHERE enrollment_key_id = ?
              AND (machine_id IS NULL OR machine_id = ?)
            """,
            (machine_id, key_id, machine_id),
        )
        if cursor.rowcount != 1:
            raise EnrollmentConsumedError("enrollment credential already consumed")

    def issue_machine(self, machine_id: str, machine_key: str) -> IssuedCredential:
        issued, salt, verifier = self._prepare(
            name=f"machine:{machine_id}",
            scopes=[Scope.SUBMIT, Scope.STATUS_WRITE],
            prefixes=[f"machines/{machine_key}"],
            machine_id=machine_id,
        )
        self._insert(issued.credential, salt, verifier)
        return issued

    def machine_credentials(self, machine_id: str) -> list[Credential]:
        rows = self.connection.execute(
            """
            SELECT * FROM credentials WHERE machine_id = ?
            ORDER BY created_at, key_id
            """,
            (machine_id,),
        ).fetchall()
        return [_credential(row) for row in rows]

    def revoke_machine_key(
        self,
        key_id: str,
        machine_id: str,
        *,
        replaced_by: str | None = None,
    ) -> Credential:
        cursor = self.connection.execute(
            """
            UPDATE credentials
            SET revoked_at = COALESCE(revoked_at, ?),
                replaced_by = COALESCE(replaced_by, ?)
            WHERE key_id = ? AND machine_id = ?
            """,
            (time.time(), replaced_by, key_id, machine_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("unknown machine credential")
        return self.get(key_id)

    def revoke(self, key_id: str) -> Credential:
        _validate_key_id(key_id)
        if self.get(key_id).machine_id is not None:
            raise MachineCredentialLifecycleError(
                "machine credentials require coordinated revocation"
            )
        now = time.time()
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE credentials SET revoked_at = COALESCE(revoked_at, ?) WHERE key_id = ?",
                (now, key_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"unknown credential: {key_id}")
        return self.get(key_id)

    def rotate(self, key_id: str) -> IssuedCredential:
        old = self.get(key_id)
        if old.machine_id is not None:
            raise MachineCredentialLifecycleError(
                "machine credentials require coordinated rotation"
            )
        if old.revoked_at is not None:
            raise ValueError("cannot rotate a revoked credential")
        issued, salt, verifier = self._prepare(
            name=old.name,
            scopes=old.scopes,
            prefixes=old.prefixes,
            expires_at=old.expires_at,
        )
        now = time.time()
        with self.connection:
            self._insert(issued.credential, salt, verifier)
            cursor = self.connection.execute(
                """
                UPDATE credentials SET revoked_at = ?, replaced_by = ?
                WHERE key_id = ? AND revoked_at IS NULL
                """,
                (now, issued.credential.key_id, key_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("credential was revoked during rotation")
        return issued


def _derive(token: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        token.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )


def _parse_token(token: str) -> str | None:
    parts = token.split(".")
    if (
        len(parts) != 3
        or parts[0] != TOKEN_PREFIX
        or KEY_ID_PATTERN.fullmatch(parts[1]) is None
        or SECRET_PATTERN.fullmatch(parts[2]) is None
    ):
        return None
    return parts[1]


def _validate_key_id(key_id: str) -> None:
    if KEY_ID_PATTERN.fullmatch(key_id) is None:
        raise ValueError("invalid credential key ID")


def _credential(row: sqlite3.Row) -> Credential:
    return Credential(
        key_id=row["key_id"],
        name=row["name"],
        scopes=frozenset(Scope(value) for value in json.loads(row["scopes"])),
        prefixes=tuple(json.loads(row["prefixes"])),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        replaced_by=row["replaced_by"],
        machine_id=row["machine_id"],
    )
