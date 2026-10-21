from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

import ctxfs.credentials as credentials_module
from ctxd.cli import main
from ctxfs.auth import Scope
from ctxfs.credentials import (
    CredentialStore,
    EnrollmentConsumedError,
    MachineCredentialLifecycleError,
)


def test_create_persists_verifier_and_verifies_token(tmp_path) -> None:
    root = tmp_path / "server"
    with CredentialStore(root) as store:
        issued = store.create(
            name="tracker", scopes=[Scope.SUBMIT], prefixes=["Laptop"]
        )
        grant = store.verify(issued.token)
        assert grant is not None
        assert grant.key_id == issued.credential.key_id
        assert grant.scopes == frozenset({Scope.SUBMIT})
        assert grant.prefixes == ("Laptop",)
        assert store.verify(issued.token + "wrong") is None

    database = (root / "credentials.sqlite3").read_bytes()
    assert issued.token.encode() not in database
    assert issued.token.rsplit(".", 1)[1].encode() not in database
    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(root / "credentials.sqlite3").st_mode & 0o777 == 0o600


def test_expired_and_revoked_credentials_do_not_verify(tmp_path) -> None:
    with CredentialStore(tmp_path) as store:
        expiring = store.create(
            name="reader", scopes=["read"], expires_at=time.time() + 60
        )
        assert store.verify(expiring.token, now=time.time() + 120) is None
        revoked = store.create(name="reader-2", scopes=["read"])
        store.revoke(revoked.credential.key_id)
        assert store.verify(revoked.token) is None


def test_rotation_invalidates_old_token_and_issues_new_key(tmp_path) -> None:
    with CredentialStore(tmp_path) as store:
        old = store.create(name="reader", scopes=["read"])
        new = store.rotate(old.credential.key_id)
        assert new.credential.key_id != old.credential.key_id
        assert store.verify(old.token) is None
        assert store.verify(new.token) is not None
        old_record = store.get(old.credential.key_id)
        assert old_record.replaced_by == new.credential.key_id
        assert old_record.revoked_at is not None


def test_store_persists_metadata_across_connections(tmp_path) -> None:
    with CredentialStore(tmp_path) as store:
        issued = store.create(name="admin", scopes=["admin"])
    with CredentialStore(tmp_path) as reopened:
        assert reopened.get(issued.credential.key_id).name == "admin"
        assert reopened.verify(issued.token) is not None


def test_scope_and_prefix_contract_is_enforced(tmp_path) -> None:
    with CredentialStore(tmp_path) as store:
        with pytest.raises(ValueError, match="at least one scope"):
            store.create(name="bad", scopes=[])
        with pytest.raises(ValueError, match="submit scope"):
            store.create(name="bad", scopes=["read"], prefixes=["Laptop"])
        with pytest.raises(ValueError, match="stand alone"):
            store.create(name="bad", scopes=["admin", "read"])


def test_untrusted_metadata_and_malformed_tokens_are_rejected(tmp_path) -> None:
    with CredentialStore(tmp_path) as store:
        with pytest.raises(ValueError, match="control characters"):
            store.create(name="unsafe\nname", scopes=["read"])
        with pytest.raises(ValueError, match="finite time"):
            store.create(name="reader", scopes=["read"], expires_at=float("nan"))
        issued = store.create(name="reader", scopes=["read"])
        key_id = issued.credential.key_id
        assert store.verify(f"ctxfs.{key_id}.{'x' * 42}") is None
        assert store.verify(f"ctxfs.{key_id}.{'x' * 44}") is None
        assert store.verify(f"ctxfs.{'x' * 10000}.secret") is None
        with pytest.raises(ValueError, match="invalid credential key ID"):
            store.get("../credentials")


def test_existing_database_permissions_are_tightened_before_use(tmp_path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    database = root / "credentials.sqlite3"
    database.touch(mode=0o644)

    with CredentialStore(root):
        assert os.stat(database).st_mode & 0o777 == 0o600


def test_cli_create_lists_and_revokes_without_redisplaying_token(
    tmp_path, capsys
) -> None:
    root_args = ["keys", "--root", str(tmp_path)]
    assert (
        main([*root_args, "create", "--name", "reader", "--scope", "read", "--json"])
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["token"].startswith(f"ctxfs.{created['key_id']}.")

    assert main([*root_args, "list", "--json"]) == 0
    listed_output = capsys.readouterr().out
    listed = json.loads(listed_output)
    assert listed[0]["key_id"] == created["key_id"]
    assert "token" not in listed[0]
    assert created["token"] not in listed_output

    assert main([*root_args, "revoke", created["key_id"], "--json"]) == 0
    revoked = json.loads(capsys.readouterr().out)
    assert revoked["active"] is False


def test_cli_generated_key_id_can_be_revoked_when_random_id_starts_with_dash(
    tmp_path, capsys, monkeypatch
) -> None:
    key_ids = iter(["-aaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"])
    monkeypatch.setattr(
        credentials_module.secrets,
        "token_urlsafe",
        lambda size: next(key_ids) if size == 12 else "s" * 43,
    )
    root_args = ["keys", "--root", str(tmp_path)]

    assert (
        main([*root_args, "create", "--name", "reader", "--scope", "read", "--json"])
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["key_id"] == "bbbbbbbbbbbbbbbb"

    assert main([*root_args, "revoke", created["key_id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is False


def test_cli_generated_key_id_can_be_rotated_when_random_id_starts_with_dash(
    tmp_path, capsys, monkeypatch
) -> None:
    key_ids = iter(
        [
            "-aaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            "-ccccccccccccccc",
            "dddddddddddddddd",
        ]
    )
    monkeypatch.setattr(
        credentials_module.secrets,
        "token_urlsafe",
        lambda size: next(key_ids) if size == 12 else "s" * 43,
    )
    root_args = ["keys", "--root", str(tmp_path)]

    assert (
        main([*root_args, "create", "--name", "reader", "--scope", "read", "--json"])
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["key_id"] == "bbbbbbbbbbbbbbbb"

    assert main([*root_args, "rotate", created["key_id"], "--json"]) == 0
    rotated = json.loads(capsys.readouterr().out)
    assert rotated["key_id"] == "dddddddddddddddd"


def test_two_store_connections_can_create_credentials(tmp_path) -> None:
    with CredentialStore(tmp_path) as first, CredentialStore(tmp_path) as second:
        one = first.create(name="one", scopes=["read"])
        two = second.create(name="two", scopes=["read"])
        assert {record.key_id for record in first.list()} == {
            one.credential.key_id,
            two.credential.key_id,
        }
        rows = (
            sqlite3.connect(first.path)
            .execute("SELECT COUNT(*) FROM credentials")
            .fetchone()
        )
        assert rows == (2,)


def test_existing_credential_database_migrates_machine_binding_column(tmp_path):
    database = tmp_path / "credentials.sqlite3"
    tmp_path.mkdir(exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE credentials (
            key_id TEXT PRIMARY KEY, name TEXT NOT NULL, scopes TEXT NOT NULL,
            prefixes TEXT NOT NULL, salt BLOB NOT NULL, verifier BLOB NOT NULL,
            created_at REAL NOT NULL, expires_at REAL, revoked_at REAL,
            replaced_by TEXT
        )
        """
    )
    connection.commit()
    connection.close()

    with CredentialStore(tmp_path) as store:
        columns = {
            row["name"]
            for row in store.connection.execute("PRAGMA table_info(credentials)")
        }
        assert "machine_id" in columns
        assert "rotation_from" in columns


def test_enrollment_use_is_single_request_replayable(tmp_path):
    with CredentialStore(tmp_path) as store:
        issued = store.create(name="enroll", scopes=["enroll"])
        with store.immediate_transaction():
            assert not store.consume_enrollment(
                issued.credential.key_id, "local-1", "fingerprint"
            )
        with store.immediate_transaction():
            assert store.consume_enrollment(
                issued.credential.key_id, "local-1", "fingerprint"
            )
        with pytest.raises(EnrollmentConsumedError):
            with store.immediate_transaction():
                store.consume_enrollment(
                    issued.credential.key_id, "local-2", "different"
                )


def test_machine_credentials_are_bound_and_require_coordinated_lifecycle(tmp_path):
    with CredentialStore(tmp_path) as store:
        with store.immediate_transaction():
            issued = store.issue_machine("machine-1", "alice-mac")
        grant = store.verify(issued.token)
        assert grant is not None
        assert grant.machine_id == "machine-1"
        assert grant.scopes == frozenset({Scope.SUBMIT, Scope.STATUS_WRITE})
        assert grant.prefixes == ("machines/alice-mac",)
        with pytest.raises(MachineCredentialLifecycleError):
            store.rotate(issued.credential.key_id)
        with pytest.raises(MachineCredentialLifecycleError):
            store.revoke(issued.credential.key_id)

    database = (tmp_path / "credentials.sqlite3").read_bytes()
    assert issued.token.encode() not in database
    assert issued.token.rsplit(".", 1)[1].encode() not in database
