from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from ctxd.ctxfs_client import CtxfsClient
from ctxd.mcp_server import (
    PROTOCOL_VERSION,
    execute_client_tool,
    handle_message,
    tool_descriptions,
)


def test_local_mcp_lists_all_ctxfs_tools_without_connection_arguments() -> None:
    tools = tool_descriptions()

    assert [tool["name"] for tool in tools] == [
        "ls",
        "tree",
        "glob",
        "stat",
        "read",
        "read_lines",
        "grep",
    ]
    for tool in tools:
        properties = tool["inputSchema"]["properties"]
        assert not {"endpoint", "root", "user_id"} & properties.keys()
    grep = next(tool for tool in tools if tool["name"] == "grep")
    assert grep["inputSchema"]["properties"]["timeout_seconds"] == {
        "type": "number",
        "minimum": 0.1,
        "maximum": 60.0,
    }
    assert "cursor" not in grep["inputSchema"]["properties"]
    description = " ".join(grep["description"].lower().split())
    steps = (
        "inspect tracked roots with the tree tool using prefix=local-files and depth=1",
        "choose the relevant canonical root, such as local-files/slack",
        "grep within that root with the prefix",
        "broaden the prefix only when",
    )
    positions = [description.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "tree tool using depth=1" not in description
    assert "such as slack" not in description
    assert "ripgrep" in description
    assert "handled automatically" in description


def test_local_mcp_initializes_and_calls_bounded_tree() -> None:
    initialize = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, Mock()
    )
    assert initialize["result"]["protocolVersion"] == PROTOCOL_VERSION

    result = SimpleNamespace(
        model_dump=lambda **kwargs: {
            "items": [],
            "complete": False,
            "stopped_by": "limit",
        }
    )
    tree = Mock(return_value=result)
    client = SimpleNamespace(files=SimpleNamespace(tree=tree))
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tree",
                "arguments": {"prefix": "local-files", "depth": 1, "limit": 10},
            },
        },
        lambda name, arguments: execute_client_tool(client, name, arguments),
    )

    tree.assert_called_once_with("local-files", depth=1, limit=10)
    assert response["result"]["structuredContent"]["stopped_by"] == "limit"


def test_local_mcp_grep_uses_real_client_files_facade(monkeypatch) -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ctxfs/grep"
        assert dict(request.url.params) == {
            "pattern": "needle",
            "prefix": "local-files/root",
            "limit": "1",
        }
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "path": "local-files/root/notes.txt",
                        "line_number": 3,
                        "line": "a needle in context",
                    }
                ],
                "complete": False,
                "stopped_by": "limit",
            },
        )

    client = CtxfsClient(endpoint="http://ctxfs.test")
    monkeypatch.setattr(
        client._async_client,
        "_build_client",
        lambda: httpx.AsyncClient(
            base_url="http://ctxfs.test",
            transport=httpx.MockTransport(handle_request),
        ),
    )

    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "grep",
                "arguments": {
                    "pattern": "needle",
                    "prefix": "local-files/root",
                    "limit": 1,
                },
            },
        },
        lambda name, arguments: execute_client_tool(client, name, arguments),
    )

    assert response["result"]["structuredContent"] == {
        "items": [
            {
                "path": "local-files/root/notes.txt",
                "line_number": 3,
                "line": "a needle in context",
                "match_start": None,
                "match_end": None,
                "content_hash": None,
            }
        ],
        "complete": True,
        "stopped_by": "limit",
    }


def test_local_mcp_returns_tool_errors_without_terminating_server() -> None:
    client = SimpleNamespace(
        files=SimpleNamespace(stat=Mock(side_effect=ValueError("missing")))
    )
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "stat", "arguments": {"path": "missing"}},
        },
        lambda name, arguments: execute_client_tool(client, name, arguments),
    )

    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == "missing"
