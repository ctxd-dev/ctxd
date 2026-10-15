from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

from ctxd.cli import main
from ctxfs.auth import Scope
from ctxfs.credentials import CredentialStore


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
        with pytest.raises(ValueError, match="submit scope"):
            store.create(name="bad", scopes=["read"], prefixes=["Laptop"])
        with pytest.raises(ValueError, match="stand alone"):
            store.create(name="bad", scopes=["admin", "read"])


def test_cli_create_lists_and_revokes_without_redisplaying_token(
    tmp_path, capsys
) -> None:
    root_args = ["keys", "--root", str(tmp_path)]
    assert main([*root_args, "create", "--name", "reader", "--scope", "read", "--json"]) == 0
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


def test_two_store_connections_can_create_credentials(tmp_path) -> None:
    with CredentialStore(tmp_path) as first, CredentialStore(tmp_path) as second:
        one = first.create(name="one", scopes=["read"])
        two = second.create(name="two", scopes=["read"])
        assert {record.key_id for record in first.list()} == {
            one.credential.key_id,
            two.credential.key_id,
        }
        rows = sqlite3.connect(first.path).execute(
            "SELECT COUNT(*) FROM credentials"
        ).fetchone()
        assert rows == (2,)
