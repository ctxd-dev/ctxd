import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ctxd.machine_identity import (
    configure_machine,
    load_or_create_machine_identity,
    machine_status,
    register_machine,
)
from ctxd.models import CtxfsMachine, CtxfsMachineRegistration
from ctxd.secure_store import (
    load_ctxfs_api_key,
    load_ctxfs_machine_registration,
    save_ctxfs_api_key,
    save_ctxfs_machine_registration,
    save_installation_proof,
)


@pytest.fixture
def local_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config.json"
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(config))
    monkeypatch.setenv("CTXD_CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    return config


def _result(*, recovered: bool = False) -> CtxfsMachineRegistration:
    return CtxfsMachineRegistration(
        machine=CtxfsMachine(
            machine_id="machine-1",
            machine_key="workstation",
            name="Workstation",
            description=None,
            state="unknown",
            platform="test-platform",
            ctxd_version="1.0",
            credential_key_id="machine-key-1",
            registered_at=1.0,
        ),
        key_id="machine-key-1",
        token="machine-secret",
        recovered=recovered,
    )


def test_identity_is_stable_and_preserves_unrelated_config(local_paths: Path) -> None:
    local_paths.write_text('{"unrelated":true}')
    first = load_or_create_machine_identity()
    second = load_or_create_machine_identity()
    assert second == first
    assert json.loads(local_paths.read_text())["unrelated"] is True


def test_copied_config_without_secure_proof_rotates_ids(
    local_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = load_or_create_machine_identity()
    credentials = tmp_path / "credentials.json"
    credentials.unlink()
    copied = load_or_create_machine_identity()
    assert copied.name == original.name
    assert copied.installation_id != original.installation_id
    assert copied.local_registration_id != original.local_registration_id


def test_endpoint_records_preserve_reader_keys_and_are_isolated(
    local_paths: Path,
) -> None:
    save_ctxfs_api_key(endpoint="https://one.example", api_key="reader")
    with patch("ctxd.machine_identity.CtxfsClient") as client:
        client.return_value.register_machine.return_value = _result()
        register_machine(endpoint="https://one.example", enrollment_token="enroll")
    assert load_ctxfs_api_key(endpoint="https://one.example") == "reader"
    assert load_ctxfs_machine_registration(endpoint="https://one.example") is not None
    assert load_ctxfs_machine_registration(endpoint="https://two.example") is None


def test_retry_reuses_exact_pending_request_after_client_failure(
    local_paths: Path,
) -> None:
    configure_machine(name="Workstation")
    with patch("ctxd.machine_identity.CtxfsClient") as client:
        client.return_value.register_machine.side_effect = RuntimeError("disconnected")
        with pytest.raises(RuntimeError, match="disconnected"):
            register_machine(endpoint="https://ctxfs.example", enrollment_token="one")

    pending = load_ctxfs_machine_registration(endpoint="https://ctxfs.example")
    assert pending is not None
    frozen = pending["request"].copy()
    configure_machine(name="Renamed")

    with patch("ctxd.machine_identity.CtxfsClient") as client:
        client.return_value.register_machine.return_value = _result(recovered=True)
        status, recovered = register_machine(
            endpoint="https://ctxfs.example", enrollment_token="one"
        )
    assert recovered is True
    assert client.return_value.register_machine.call_args.kwargs == {
        **frozen,
        "enrollment_token": "one",
    }
    assert status.registration_state == "registered"
    assert status.metadata_sync == "pending_server_update"


@pytest.mark.parametrize("stale_state", ["pending", "registered"])
def test_copied_config_supersedes_same_endpoint_registration(
    local_paths: Path, stale_state: str
) -> None:
    endpoint = "https://ctxfs.example"
    other_endpoint = "https://other.example"
    original = load_or_create_machine_identity()
    stale_request = {
        "local_registration_id": original.local_registration_id,
        "name": original.name,
        "description": original.description,
        "platform": "old-platform",
        "ctxd_version": "old-version",
    }
    stale_registration = {
        "state": stale_state,
        "installation_id": original.installation_id,
        "request": stale_request,
    }
    if stale_state == "registered":
        stale_registration.update(
            {
                "machine": _result().machine.model_dump(mode="json"),
                "key_id": "machine-key-1",
                "token": "stale-machine-secret",
            }
        )
    save_ctxfs_machine_registration(endpoint=endpoint, registration=stale_registration)
    other_registration = {
        "state": "pending",
        "installation_id": original.installation_id,
        "request": stale_request,
    }
    save_ctxfs_machine_registration(
        endpoint=other_endpoint, registration=other_registration
    )
    save_ctxfs_api_key(endpoint=endpoint, api_key="reader-key")
    save_installation_proof("00000000-0000-0000-0000-000000000001")

    def assert_pending_replacement(**request):
        pending = load_ctxfs_machine_registration(endpoint=endpoint)
        assert pending == {
            "state": "pending",
            "installation_id": request_identity.installation_id,
            "request": {
                key: value
                for key, value in request.items()
                if key != "enrollment_token"
            },
        }
        assert "token" not in pending
        return _result()

    request_identity = load_or_create_machine_identity()
    assert request_identity.installation_id != original.installation_id
    assert request_identity.local_registration_id != original.local_registration_id
    with patch("ctxd.machine_identity.CtxfsClient") as client:
        client.return_value.register_machine.side_effect = assert_pending_replacement
        status, _ = register_machine(endpoint=endpoint, enrollment_token="enroll")

    request = client.return_value.register_machine.call_args.kwargs
    assert request["local_registration_id"] == request_identity.local_registration_id
    assert request["enrollment_token"] == "enroll"
    assert status.installation_id == request_identity.installation_id
    assert status.registration_state == "registered"
    current = load_ctxfs_machine_registration(endpoint=endpoint)
    assert current is not None
    assert current["installation_id"] == request_identity.installation_id
    assert current["state"] == "registered"
    assert current["token"] == "machine-secret"
    assert (
        load_ctxfs_machine_registration(endpoint=other_endpoint) == other_registration
    )
    assert load_ctxfs_api_key(endpoint=endpoint) == "reader-key"


def test_mismatched_registration_response_keeps_pending_state(
    local_paths: Path,
) -> None:
    result = _result().model_copy(deep=True)
    result.machine.credential_key_id = "different-key"
    with patch("ctxd.machine_identity.CtxfsClient") as client:
        client.return_value.register_machine.return_value = result
        with pytest.raises(Exception, match="credential binding"):
            register_machine(endpoint="https://ctxfs.example", enrollment_token="one")
    stored = load_ctxfs_machine_registration(endpoint="https://ctxfs.example")
    assert stored is not None and stored["state"] == "pending"


def test_response_save_failure_keeps_pending_state_for_retry(
    local_paths: Path,
) -> None:
    from ctxd import machine_identity

    real_save = machine_identity.save_ctxfs_machine_registration
    saves = 0

    def fail_second_save(**kwargs):
        nonlocal saves
        saves += 1
        if saves == 2:
            raise OSError("disk full")
        return real_save(**kwargs)

    with patch("ctxd.machine_identity.CtxfsClient") as client, patch(
        "ctxd.machine_identity.save_ctxfs_machine_registration",
        side_effect=fail_second_save,
    ):
        client.return_value.register_machine.return_value = _result()
        with pytest.raises(OSError, match="disk full"):
            register_machine(endpoint="https://ctxfs.example", enrollment_token="one")
    stored = load_ctxfs_machine_registration(endpoint="https://ctxfs.example")
    assert stored is not None and stored["state"] == "pending"


def test_local_status_never_contains_machine_token(local_paths: Path) -> None:
    with patch("ctxd.machine_identity.CtxfsClient") as client:
        client.return_value.register_machine.return_value = _result()
        register_machine(endpoint="https://ctxfs.example", enrollment_token="enroll")
    payload = machine_status(endpoint="https://ctxfs.example").as_dict()
    assert "machine-secret" not in json.dumps(payload)
    assert "token" not in payload


def test_malformed_machine_config_fails_closed(local_paths: Path) -> None:
    local_paths.write_text('{"machine":{"schema_version":1}}')
    with pytest.raises(ValueError, match="machine configuration"):
        load_or_create_machine_identity()


@pytest.mark.parametrize("contents", ["{", "[]", '"scalar"'])
def test_invalid_config_is_not_overwritten_or_used(
    local_paths: Path, tmp_path: Path, contents: str
) -> None:
    local_paths.write_text(contents)
    credentials = tmp_path / "credentials.json"
    with patch("ctxd.machine_identity.CtxfsClient") as client:
        with pytest.raises(ValueError, match="configuration"):
            register_machine(endpoint="https://ctxfs.example", enrollment_token="one")
    assert local_paths.read_text() == contents
    assert not credentials.exists()
    client.assert_not_called()


def test_unreadable_config_is_not_treated_as_missing(local_paths: Path) -> None:
    local_paths.write_text('{"unrelated":true}')
    original = local_paths.read_bytes()
    with patch("pathlib.Path.read_text", side_effect=OSError("denied")), patch(
        "ctxd.machine_identity.CtxfsClient"
    ) as client:
        with pytest.raises(ValueError, match="could not be read"):
            register_machine(endpoint="https://ctxfs.example", enrollment_token="one")
    assert local_paths.read_bytes() == original
    client.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "compromised"),
        ("registered_at", float("inf")),
        ("registered_at", 0.0),
        ("platform", ""),
        ("platform", "   "),
        ("ctxd_version", "bad\nversion"),
        ("name", "x" * 129),
        ("description", "bad\x7fdescription"),
    ],
)
def test_invalid_registration_response_keeps_pending_without_token(
    local_paths: Path, field: str, value: object
) -> None:
    result = _result()
    object.__setattr__(result.machine, field, value)
    with patch("ctxd.machine_identity.CtxfsClient") as client:
        client.return_value.register_machine.return_value = result
        with pytest.raises(Exception):
            register_machine(endpoint="https://ctxfs.example", enrollment_token="one")
    stored = load_ctxfs_machine_registration(endpoint="https://ctxfs.example")
    assert stored is not None and stored["state"] == "pending"
    assert "token" not in stored
