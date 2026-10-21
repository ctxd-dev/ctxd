from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

import ctxd
from ctxd import AsyncClient, Client, CtxfsClient
from ctxd.config import (
    DEFAULT_CTXFS_SOCKET,
    CtxfsEndpointResolution,
    clear_ctxfs_api_key,
    normalize_ctxfs_endpoint,
    resolve_ctxfs_api_key,
    resolve_ctxfs_endpoint,
    resolve_ctxfs_endpoint_details,
    save_ctxfs_api_key,
)
from ctxd.exceptions import CtxdError


def test_removed_sdk_interfaces_are_absent() -> None:
    for name in (
        "SearchResult",
        "DocumentResult",
        "DEFAULT_BACKEND",
        "DEFAULT_BASE_URL",
    ):
        assert not hasattr(ctxd, name)
    for client in (Client(), AsyncClient()):
        for name in ("search", "fetch", "fetch_document", "profile", "get_profile"):
            assert not hasattr(client, name)


def test_client_accepts_ctxfs_endpoint_without_backend() -> None:
    assert Client(ctxfs_endpoint="https://ctxfs.example", ctxfs_api_key="token").files


def test_sync_files_start_managed_local_service_on_first_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = Mock()
    monkeypatch.setattr(
        "ctxd.ctxfs_client.resolve_ctxfs_endpoint_details",
        lambda **kwargs: CtxfsEndpointResolution(
            "unix:///tmp/managed-ctxfs.sock", "default-managed-socket", True
        ),
    )
    monkeypatch.setattr("ctxd.ctxfs_client._ensure_managed_local_started", start)

    async def mock_request(self, method, url, **kwargs):
        del self, kwargs
        assert method == "GET" and url == "/api/ctxfs/stat"
        return httpx.Response(200, json={"path": "doc.txt", "kind": "file", "size": 3})

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)

    client = Client()
    start.assert_not_called()
    client.files.stat("doc.txt")

    start.assert_called_once_with()


@pytest.mark.asyncio
async def test_async_files_start_managed_local_service_on_first_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = Mock()
    monkeypatch.setattr(
        "ctxd.ctxfs_client.resolve_ctxfs_endpoint_details",
        lambda **kwargs: CtxfsEndpointResolution(
            "unix:///tmp/managed-ctxfs.sock", "default-managed-socket", True
        ),
    )
    monkeypatch.setattr("ctxd.ctxfs_client._ensure_managed_local_started", start)

    async def mock_request(self, method, url, **kwargs):
        del self, kwargs
        assert method == "GET" and url == "/api/ctxfs/stat"
        return httpx.Response(200, json={"path": "doc.txt", "kind": "file", "size": 3})

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    client = AsyncClient()
    start.assert_not_called()
    await client.files.stat("doc.txt")

    start.assert_called_once_with()


@pytest.mark.parametrize(
    ("client_kwargs"),
    (
        {"ctxfs_endpoint": "https://ctxfs.example"},
        {"ctxfs_socket": "/tmp/explicit-ctxfs.sock"},
    ),
)
def test_sync_files_do_not_start_service_for_explicit_endpoints(
    monkeypatch: pytest.MonkeyPatch, client_kwargs: dict[str, str]
) -> None:
    start = Mock()
    monkeypatch.setattr("ctxd.ctxfs_client._ensure_managed_local_started", start)

    async def mock_request(self, method, url, **kwargs):
        del self, method, url, kwargs
        return httpx.Response(200, json={"path": "doc.txt", "kind": "file", "size": 3})

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    Client(**client_kwargs).files.stat("doc.txt")
    start.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_kwargs"),
    (
        {"ctxfs_endpoint": "https://ctxfs.example"},
        {"ctxfs_socket": "/tmp/explicit-ctxfs.sock"},
    ),
)
async def test_async_files_do_not_start_service_for_explicit_endpoints(
    monkeypatch: pytest.MonkeyPatch, client_kwargs: dict[str, str]
) -> None:
    start = Mock()
    monkeypatch.setattr("ctxd.ctxfs_client._ensure_managed_local_started", start)

    async def mock_request(self, method, url, **kwargs):
        del self, method, url, kwargs
        return httpx.Response(200, json={"path": "doc.txt", "kind": "file", "size": 3})

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    await AsyncClient(**client_kwargs).files.stat("doc.txt")
    start.assert_not_called()


def test_ctxfs_endpoint_prefers_socket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTXD_CTXFS_URL", raising=False)
    monkeypatch.setenv("CTXD_CTXFS_SOCKET", "/tmp/ctxfs.sock")
    assert resolve_ctxfs_endpoint() == "unix:///tmp/ctxfs.sock"


def test_ctxfs_endpoint_rejects_paths_that_change_credential_scope() -> None:
    with pytest.raises(ValueError, match="must not contain a path"):
        normalize_ctxfs_endpoint("https://ctxfs.example/tenant-a")


def test_ctxfs_credentials_are_normalized_and_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("CTXD_CTXFS_API_KEY", raising=False)
    save_ctxfs_api_key("one-token", endpoint="HTTPS://ONE.example:443/")
    save_ctxfs_api_key("two-token", endpoint="https://two.example")
    assert normalize_ctxfs_endpoint("HTTPS://ONE.example:443/") == "https://one.example"
    assert resolve_ctxfs_api_key(endpoint="https://one.example/") == "one-token"
    assert resolve_ctxfs_api_key(endpoint="https://two.example") == "two-token"
    clear_ctxfs_api_key(endpoint="https://one.example")
    assert resolve_ctxfs_api_key(endpoint="https://one.example") is None


def test_ctxfs_credential_precedence_and_unix_socket_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    save_ctxfs_api_key("stored", endpoint="https://ctxfs.example")
    monkeypatch.setenv("CTXD_CTXFS_API_KEY", "environment")
    assert (
        resolve_ctxfs_api_key("explicit", endpoint="https://ctxfs.example")
        == "explicit"
    )
    assert resolve_ctxfs_api_key(endpoint="https://ctxfs.example") == "environment"
    assert resolve_ctxfs_api_key("explicit", endpoint="unix:///tmp/ctxfs.sock") is None


def test_ctxfs_http_client_sends_endpoint_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CTXD_CTXFS_API_KEY", "ctxfs-secret")
    client = CtxfsClient(endpoint="https://ctxfs.example")

    async def mock_request(self, method, url, *, params=None, headers=None):
        assert method == "GET" and url == "/health"
        assert headers["Authorization"] == "Bearer ctxfs-secret"
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    assert client.status().status == "ok"


def test_machine_registration_uses_one_request_enrollment_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = CtxfsClient(endpoint="https://ctxfs.example", api_key="reader-token")

    async def mock_request(self, method, url, **kwargs):
        del self
        assert method == "POST" and url == "/api/ctxfs/machines/register"
        assert kwargs["headers"]["Authorization"] == "Bearer enrollment-token"
        return httpx.Response(
            201,
            json={
                "machine": {
                    "machine_id": "machine-1",
                    "machine_key": "workstation",
                    "name": "Workstation",
                    "description": None,
                    "state": "unknown",
                    "platform": "test",
                    "ctxd_version": "1.0",
                    "credential_key_id": "key-1",
                    "registered_at": 1.0,
                },
                "key_id": "key-1",
                "token": "machine-token",
            },
        )

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    result = client.register_machine(
        local_registration_id="local-1",
        name="Workstation",
        description=None,
        platform="test",
        ctxd_version="1.0",
        enrollment_token="enrollment-token",
    )
    assert result.recovered is False
    assert result.token == "machine-token"


def test_ctxfs_http_mcp_uses_endpoint_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(
        ctxfs_endpoint="https://ctxfs.example",
        ctxfs_api_key="mcp-token",
    )

    async def mock_request(self, method, url, **kwargs):
        del self
        assert method == "POST"
        assert url == "/mcp"
        assert kwargs["headers"]["Authorization"] == "Bearer mcp-token"
        assert kwargs["json"]["params"] == {
            "name": "tree",
            "arguments": {"prefix": "Docs"},
        }
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"items": []}},
        )

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    assert client.call_tool("tree", {"prefix": "Docs"}) == {"items": []}


def test_ctxfs_authentication_validation_distinguishes_forbidden_from_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = CtxfsClient(
        endpoint="https://ctxfs.example",
        api_key="candidate-token",
    )
    responses = iter(
        (
            httpx.Response(403, json={"detail": "scope_denied"}),
            httpx.Response(401, json={"detail": "invalid bearer credential"}),
        )
    )

    async def mock_request(self, method, url, **kwargs):
        del self, kwargs
        assert method == "GET"
        assert url == "/health"
        return next(responses)

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    client.validate_authentication()
    with pytest.raises(CtxdError) as exc_info:
        client.validate_authentication()
    assert exc_info.value.status_code == 401


def test_synchronous_files_client_exposes_ctxfs_grep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client(ctxfs_endpoint="http://127.0.0.1:8765")

    async def mock_request(self, method, url, *, params=None, headers=None):
        assert method == "GET" and url == "/api/ctxfs/grep"
        assert params == {"pattern": "needle", "prefix": "root", "limit": 5}
        return httpx.Response(
            200,
            json={
                "items": [{"path": "root/a", "line_number": 1, "line": "needle"}],
                "complete": True,
            },
        )

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    assert client.files.grep("needle", prefix="root", limit=5).items[0].line == "needle"


def test_default_endpoint_is_local_ctxfs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("CTXD_CTXFS_URL", raising=False)
    monkeypatch.delenv("CTXD_CTXFS_SOCKET", raising=False)
    monkeypatch.setattr(
        "ctxd.config._resolve_local_daemon_ctxfs_endpoint", lambda: None
    )
    resolution = resolve_ctxfs_endpoint_details()
    assert resolution.endpoint == f"unix://{DEFAULT_CTXFS_SOCKET}"
    assert resolution.managed_local is True


def test_only_default_socket_is_owned_by_managed_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("CTXD_CTXFS_URL", raising=False)
    monkeypatch.delenv("CTXD_CTXFS_SOCKET", raising=False)
    monkeypatch.setattr(
        "ctxd.config._resolve_local_daemon_ctxfs_endpoint", lambda: None
    )

    default = resolve_ctxfs_endpoint_details()
    explicit = resolve_ctxfs_endpoint_details(socket_path="/tmp/external.sock")

    assert (default.source, default.managed_local) == ("default-managed-socket", True)
    assert explicit.endpoint == "unix:///tmp/external.sock"
    assert (explicit.source, explicit.managed_local) == ("argument-socket", False)


def test_ctxfs_url_has_precedence_over_socket_and_legacy_hosted_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CTXD_CTXFS_URL", "https://ctxfs.example.net")
    monkeypatch.setenv("CTXD_CTXFS_SOCKET", "/tmp/lower-priority.sock")
    monkeypatch.setenv("CTXD_BASE_URL", "https://hosted.example")
    monkeypatch.setenv("CTXD_API_KEY", "hosted-token")

    resolution = resolve_ctxfs_endpoint_details()

    assert resolution.endpoint == "https://ctxfs.example.net"
    assert resolution.source == "environment-url"
    assert resolution.managed_local is False


@pytest.mark.parametrize(
    ("socket_path", "expected"),
    [
        ('"/tmp/custom.sock"', "unix:///tmp/custom.sock"),
        ('"none"', "http://127.0.0.2:9999"),
    ],
)
def test_ctxfs_endpoint_reads_daemon_socket_or_http_configuration(
    socket_path: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("CTXD_CTXFS_URL", raising=False)
    monkeypatch.delenv("CTXD_CTXFS_SOCKET", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    daemon_config_dir = tmp_path / ".ctxd" / "local"
    daemon_config_dir.mkdir(parents=True)
    (daemon_config_dir / "config.toml").write_text(
        f'[ctxfs]\nhost = "127.0.0.2"\nport = 9999\nsocket_path = {socket_path}\n'
    )

    resolution = resolve_ctxfs_endpoint_details()
    assert resolution.endpoint == expected
    assert resolution.source == "daemon-config"
    assert resolution.managed_local is False


def test_ctxfs_stat_accepts_numeric_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(ctxfs_endpoint="http://127.0.0.1:8765")

    async def mock_request(self, method, url, *, params=None, headers=None):
        del self, headers
        assert method == "GET"
        assert url == "/api/ctxfs/stat"
        assert params == {"path": "local-files/root/README.md"}
        return httpx.Response(
            200,
            json={
                "path": "local-files/root/README.md",
                "kind": "file",
                "created_at": 1_792_017_065.25,
                "updated_at": 1_792_017_072.5,
            },
        )

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    entry = client.files.stat("local-files/root/README.md")
    assert entry.created_at == 1_792_017_065.25
    assert entry.updated_at == 1_792_017_072.5
