from types import SimpleNamespace
from unittest.mock import Mock

from ctxd.mcp_server import PROTOCOL_VERSION, handle_message, tool_descriptions


def test_local_mcp_lists_all_ctxfs_tools_without_connection_arguments() -> None:
    tools = tool_descriptions()

    assert [tool["name"] for tool in tools] == [
        "ls", "tree", "glob", "stat", "read", "read_lines", "grep"
    ]
    for tool in tools:
        properties = tool["inputSchema"]["properties"]
        assert not {"endpoint", "root", "user_id"} & properties.keys()


def test_local_mcp_initializes_and_calls_bounded_tree() -> None:
    initialize = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, Mock()
    )
    assert initialize["result"]["protocolVersion"] == PROTOCOL_VERSION

    result = SimpleNamespace(
        model_dump=lambda **kwargs: {
            "items": [], "complete": False, "stopped_by": "limit"
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
                "arguments": {"prefix": "local-files/root", "depth": 2, "limit": 10},
            },
        },
        client,
    )

    tree.assert_called_once_with("local-files/root", depth=2, limit=10)
    assert response["result"]["structuredContent"]["stopped_by"] == "limit"


def test_local_mcp_returns_tool_errors_without_terminating_server() -> None:
    client = SimpleNamespace(files=SimpleNamespace(stat=Mock(side_effect=ValueError("missing"))))
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "stat", "arguments": {"path": "missing"}},
        },
        client,
    )

    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == "missing"
