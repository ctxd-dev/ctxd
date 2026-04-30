import json
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from ctxd import SDK_NAME, AsyncClient, Client, __version__, get_user_agent
from ctxd._metadata import SDK_VERSION
from ctxd.config import clear_api_key, resolve_api_key, save_api_key
from ctxd.exceptions import CtxdAuthError, CtxdError, CtxdProtocolError


def test_sdk_user_agent_is_versioned() -> None:
    assert SDK_NAME == "ctxd"
    assert SDK_VERSION == version("ctxd")
    assert __version__ == SDK_VERSION
    assert get_user_agent() == f"{SDK_NAME}/{SDK_VERSION}"


def test_sdk_search_parses_mcp_sse_response() -> None:
    client = Client(base_url="https://ctxd.example.com", api_key="test-token")
    payload = {
        "results": [
            {
                "id": "doc-1",
                "title": "Deployment notes",
                "url": "slack://general/doc-1",
                "text": "Deploy completed successfully.",
                "metadata": {},
            }
        ]
    }
    envelope = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload),
                }
            ]
        }
    }
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=f"data: {json.dumps(envelope)}\n\n",
    )

    async def mock_post(self, url, *, headers, json):
        assert url == "https://ctxd.example.com/mcp"
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["User-Agent"] == get_user_agent()
        assert json["method"] == "tools/call"
        assert json["params"]["name"] == "search"
        assert json["params"]["arguments"] == {"query": "deployment"}
        return response

    with patch("httpx.AsyncClient.post", mock_post):
        search_result = client.search("deployment")

    assert search_result.results[0].id == "doc-1"
    assert search_result.results[0].title == "Deployment notes"


def test_sdk_search_accepts_document_uid_and_snippet_fields() -> None:
    client = Client(base_url="https://ctxd.example.com", api_key="test-token")
    payload = {
        "results": [
            {
                "document_uid": "doc-1",
                "app_name": "slack",
                "title": "Deployment notes",
                "url": "slack://general/doc-1",
                "snippet": "Deploy completed successfully.",
                "is_snippet": True,
                "requires_fetch_for_full_text": True,
                "metadata": {},
            }
        ]
    }
    envelope = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload),
                }
            ]
        }
    }
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=f"data: {json.dumps(envelope)}\n\n",
    )

    async def mock_post(self, url, *, headers, json):
        del self, url, headers, json
        return response

    with patch("httpx.AsyncClient.post", mock_post):
        search_result = client.search("deployment")

    assert search_result.results[0].id == "doc-1"
    assert search_result.results[0].app_name == "slack"
    assert search_result.results[0].text == "Deploy completed successfully."


def test_sdk_parse_json_payload_raises_ctxd_error_for_mcp_is_error() -> None:
    body = {
        "result": {
            "isError": True,
            "content": [{"type": "text", "text": "Something went wrong"}],
        }
    }

    with pytest.raises(CtxdError, match="Something went wrong"):
        AsyncClient._parse_json_payload(body)


def test_sdk_parse_json_payload_wraps_invalid_json_text() -> None:
    body = {
        "result": {
            "content": [{"type": "text", "text": "this is not valid json"}],
        }
    }

    with pytest.raises(CtxdProtocolError, match="not valid JSON"):
        AsyncClient._parse_json_payload(body)


def test_sdk_wraps_network_errors() -> None:
    client = Client(base_url="https://ctxd.example.com", api_key="test-token")

    async def mock_post(self, url, *, headers, json):
        del self, url, headers, json
        raise httpx.ConnectError("[Errno 8] nodename nor servname provided")

    with patch("httpx.AsyncClient.post", mock_post), pytest.raises(
        CtxdError,
        match=(
            r"Could not connect to ctxd at https://ctxd\.example\.com/mcp\. "
            r"Check your internet connection and try again\."
        ),
    ):
        client.search("deployment")


def test_client_search_uses_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTXD_API_KEY", "ctxd-api-key")

    client = Client(base_url="https://ctxd.example.com")
    payload = {
        "results": [
            {
                "document_uid": "doc-1",
                "app_name": "slack",
                "title": "Deployment notes",
                "url": "slack://general/doc-1",
                "snippet": "Deploy completed successfully.",
                "metadata": {},
            }
        ]
    }
    envelope = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload),
                }
            ]
        }
    }
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=f"data: {json.dumps(envelope)}\n\n",
    )

    async def mock_post(self, url, *, headers, json):
        del self, url, json
        assert headers["Authorization"] == "Bearer ctxd-api-key"
        return response

    with patch("httpx.AsyncClient.post", mock_post):
        search_result = client.search("deployment")

    assert search_result.results[0].id == "doc-1"


def test_sdk_fetch_document_accepts_app_name() -> None:
    client = Client(base_url="https://ctxd.example.com", api_key="test-token")
    payload = {
        "id": "doc-1",
        "app_name": "slack",
        "title": "Deployment notes",
        "url": "slack://general/doc-1",
        "text": "Deploy completed successfully.",
        "metadata": {},
    }
    envelope = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload),
                }
            ]
        }
    }
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=f"data: {json.dumps(envelope)}\n\n",
    )

    async def mock_post(self, url, *, headers, json):
        del self, url, headers, json
        return response

    with patch("httpx.AsyncClient.post", mock_post):
        document = client.fetch_document("doc-1")

    assert document.id == "doc-1"
    assert document.app_name == "slack"


def test_saved_api_key_is_resolved_from_plaintext_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))

    save_api_key("stored-api-key", base_url="https://api.ctxd.dev")

    assert resolve_api_key() == "stored-api-key"
    assert (tmp_path / "credentials.json").read_text() == (
        '{\n  "api_key": "stored-api-key"\n}\n'
    )


def test_saved_api_key_is_global_across_base_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))

    save_api_key("prod-key", base_url="https://mcp.ctxd.dev")
    save_api_key("staging-key", base_url="https://mcp.staging.ctxd.dev")
    monkeypatch.setenv("CTXD_BASE_URL", "https://mcp.staging.ctxd.dev")

    assert resolve_api_key() == "staging-key"


def test_saved_api_key_resolves_for_normalized_mcp_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))

    save_api_key("stored-api-key", base_url="https://mcp.ctxd.dev")

    assert resolve_api_key(base_url="https://mcp.ctxd.dev/mcp") == "stored-api-key"


def test_async_client_requires_api_key() -> None:
    client = AsyncClient(base_url="https://ctxd.example.com")

    with pytest.raises(
        CtxdAuthError,
        match=r"Missing API key\. Set `CTXD_API_KEY`, run `ctxd login`, or pass `api_key=`\.",
    ):
        Client._run(client._resolve_access_token())


def test_clear_api_key_keeps_base_url_and_removes_plaintext_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(tmp_path / "config.json"))
    save_api_key("stored-api-key", base_url="https://ctxd.internal")

    credentials_path = tmp_path / "credentials.json"
    assert credentials_path.exists()

    clear_api_key()

    assert (tmp_path / "config.json").read_text() == (
        '{\n  "base_url": "https://ctxd.internal"\n}\n'
    )
    assert not credentials_path.exists()


def test_client_context_manager_returns_client() -> None:
    with Client(api_key="test-token", base_url="https://ctxd.example.com") as client:
        assert client.base_url == "https://ctxd.example.com/mcp"


def test_client_context_manager_does_not_hold_open_async_client() -> None:
    with Client(api_key="test-token", base_url="https://ctxd.example.com") as client:
        assert client._async_client._client is None

    assert client._async_client._client is None


@pytest.mark.asyncio
async def test_async_client_search_parses_mcp_sse_response() -> None:
    client = AsyncClient(base_url="https://ctxd.example.com", api_key="test-token")
    payload = {
        "results": [
            {
                "id": "doc-2",
                "title": "Incident notes",
                "url": "slack://incidents/doc-2",
                "text": "Incident resolved.",
                "metadata": {},
            }
        ]
    }
    envelope = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload),
                }
            ]
        }
    }
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=f"data: {json.dumps(envelope)}\n\n",
    )

    async def mock_post(self, url, *, headers, json):
        del self, json
        assert url == "https://ctxd.example.com/mcp"
        assert headers["Authorization"] == "Bearer test-token"
        return response

    with patch("httpx.AsyncClient.post", mock_post):
        result = await client.search("incident")

    assert result.results[0].id == "doc-2"
