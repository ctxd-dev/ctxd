from pathlib import Path

import httpx
import pytest

import ctxd
from ctxd import AsyncClient, Client, CtxfsClient
from ctxd.config import DEFAULT_CTXFS_SOCKET, clear_ctxfs_api_key, normalize_ctxfs_endpoint, resolve_ctxfs_api_key, resolve_ctxfs_endpoint, resolve_ctxfs_endpoint_details, save_ctxfs_api_key


def test_removed_sdk_interfaces_are_absent() -> None:
    for name in ("SearchResult", "DocumentResult", "DEFAULT_BACKEND", "DEFAULT_BASE_URL"):
        assert not hasattr(ctxd, name)
    for client in (Client(), AsyncClient()):
        for name in ("search", "fetch", "fetch_document", "profile", "get_profile"):
            assert not hasattr(client, name)


def test_client_accepts_ctxfs_endpoint_without_backend() -> None:
    assert Client(ctxfs_endpoint="https://ctxfs.example", ctxfs_api_key="token").files


def test_ctxfs_endpoint_prefers_socket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CTXD_CTXFS_URL", raising=False)
    monkeypatch.setenv("CTXD_CTXFS_SOCKET", "/tmp/ctxfs.sock")
    assert resolve_ctxfs_endpoint() == "unix:///tmp/ctxfs.sock"


def test_ctxfs_credentials_are_normalized_and_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("CTXD_CTXFS_API_KEY", raising=False)
    save_ctxfs_api_key("one-token", endpoint="HTTPS://ONE.example:443/")
    save_ctxfs_api_key("two-token", endpoint="https://two.example")
    assert normalize_ctxfs_endpoint("HTTPS://ONE.example:443/") == "https://one.example"
    assert resolve_ctxfs_api_key(endpoint="https://one.example/") == "one-token"
    assert resolve_ctxfs_api_key(endpoint="https://two.example") == "two-token"
    clear_ctxfs_api_key(endpoint="https://one.example")
    assert resolve_ctxfs_api_key(endpoint="https://one.example") is None


def test_ctxfs_credential_precedence_and_unix_socket_trust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    save_ctxfs_api_key("stored", endpoint="https://ctxfs.example")
    monkeypatch.setenv("CTXD_CTXFS_API_KEY", "environment")
    assert resolve_ctxfs_api_key("explicit", endpoint="https://ctxfs.example") == "explicit"
    assert resolve_ctxfs_api_key(endpoint="https://ctxfs.example") == "environment"
    assert resolve_ctxfs_api_key("explicit", endpoint="unix:///tmp/ctxfs.sock") is None


def test_ctxfs_http_client_sends_endpoint_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTXD_CTXFS_API_KEY", "ctxfs-secret")
    client = CtxfsClient(endpoint="https://ctxfs.example")

    async def mock_request(self, method, url, *, params=None, headers=None):
        assert method == "GET" and url == "/health"
        assert headers["Authorization"] == "Bearer ctxfs-secret"
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    assert client.status().status == "ok"


def test_synchronous_files_client_exposes_ctxfs_grep(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(ctxfs_endpoint="http://127.0.0.1:8765")

    async def mock_request(self, method, url, *, params=None, headers=None):
        assert method == "GET" and url == "/api/ctxfs/grep"
        assert params == {"pattern": "needle", "prefix": "root", "limit": 5}
        return httpx.Response(200, json={"items": [{"path": "root/a", "line_number": 1, "line": "needle"}], "complete": True})

    monkeypatch.setattr("httpx.AsyncClient.request", mock_request)
    assert client.files.grep("needle", prefix="root", limit=5).items[0].line == "needle"


def test_default_endpoint_is_local_ctxfs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("CTXD_CTXFS_URL", raising=False)
    monkeypatch.delenv("CTXD_CTXFS_SOCKET", raising=False)
    monkeypatch.setattr("ctxd.config._resolve_local_daemon_ctxfs_endpoint", lambda: None)
    resolution = resolve_ctxfs_endpoint_details()
    assert resolution.endpoint == f"unix://{DEFAULT_CTXFS_SOCKET}"
    assert resolution.managed_local is True
