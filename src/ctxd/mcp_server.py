"""Local stdio MCP adapter for ctxfs."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from ctxd.ctxfs_client import CtxfsClient

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    (
        "ls",
        "List a ctxfs directory",
        {"path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}},
        [],
    ),
    (
        "tree",
        "List a bounded ctxfs tree",
        {
            "prefix": {"type": "string"},
            "depth": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1},
        },
        [],
    ),
    (
        "glob",
        "Match ctxfs paths",
        {
            "pattern": {"type": "string"},
            "prefix": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
        },
        ["pattern"],
    ),
    ("stat", "Read ctxfs path metadata", {"path": {"type": "string"}}, ["path"]),
    (
        "read",
        "Read a ctxfs file",
        {"path": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1}},
        ["path"],
    ),
    (
        "read_lines",
        "Read a ctxfs line range",
        {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        ["path", "start_line", "end_line"],
    ),
    (
        "grep",
        (
            "Search ctxfs file contents using ripgrep regular-expression syntax. "
            "Results are ordered by path and line. Retrieval method: (1) inspect "
            "top-level folders with tree or glob, (2) choose the relevant source "
            "folder, such as slack/, (3) grep within that prefix, and (4) broaden "
            "the prefix only when the scoped search does not find enough context. "
            "Transport continuations are handled automatically; a failure means "
            "the result was not exhaustive."
        ),
        {
            "pattern": {"type": "string"},
            "prefix": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
            "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 60.0},
        },
        ["pattern"],
    ),
]


def tool_descriptions() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }
        for name, description, properties, required in TOOLS
    ]


ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


def handle_message(
    message: dict[str, Any], execute_tool: ToolExecutor
) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ctxd-local-ctxfs", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tool_descriptions()})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            payload = execute_tool(params.get("name"), params.get("arguments") or {})
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "structuredContent": payload,
                },
            )
        except Exception as exc:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
    return _error(request_id, -32601, f"Method not found: {method}")


def execute_client_tool(
    client: CtxfsClient, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    files = client.files
    if name == "ls":
        value = files.ls(arguments.get("path", ""), limit=arguments.get("limit"))
    elif name == "tree":
        value = files.tree(
            arguments.get("prefix", ""),
            depth=arguments.get("depth"),
            limit=arguments.get("limit"),
        )
    elif name == "glob":
        value = files.glob(
            arguments["pattern"],
            prefix=arguments.get("prefix", ""),
            limit=arguments.get("limit"),
        )
    elif name == "stat":
        value = files.stat(arguments["path"])
    elif name == "read":
        value = files.read(arguments["path"], max_bytes=arguments.get("max_bytes"))
    elif name == "read_lines":
        value = files.read_lines(
            arguments["path"],
            start_line=arguments["start_line"],
            end_line=arguments["end_line"],
        )
    elif name == "grep":
        value = files.grep(
            arguments["pattern"],
            prefix=arguments.get("prefix", ""),
            limit=arguments.get("limit"),
            timeout_seconds=arguments.get("timeout_seconds"),
        )
    else:
        raise ValueError(f"Unknown ctxfs tool: {name}")
    return value.model_dump(mode="json")


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    client = CtxfsClient()
    for line in stdin:
        try:
            message = json.loads(line)
            response = handle_message(
                message,
                lambda name, arguments: execute_client_tool(client, name, arguments),
            )
        except Exception as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
