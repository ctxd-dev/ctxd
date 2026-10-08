import json
from typing import Any

import httpx

from ctxd._metadata import get_user_agent
from ctxd.config import resolve_api_key, resolve_backend, resolve_base_url
from ctxd.ctxfs_client import AsyncCtxfsClient
from ctxd.exceptions import CtxdAuthError, CtxdError, CtxdProtocolError
from ctxd.models import DocumentResult, ProfileResult, SearchItem, SearchResult


class AsyncClient:
    """Async client for the public ctxd MCP endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        backend: str | None = None,
        ctxfs_endpoint: str | None = None,
        ctxfs_socket: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._backend = resolve_backend(backend)
        self._base_url = self._normalize_base_url(resolve_base_url(base_url))
        self._api_key = (
            resolve_api_key(api_key, base_url=self._base_url)
            if self._backend == "hosted"
            else None
        )
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._ctxfs_client = (
            AsyncCtxfsClient(
                endpoint=ctxfs_endpoint,
                socket_path=ctxfs_socket,
                timeout=timeout,
            )
            if self._backend == "ctxfs"
            else None
        )
        self.files = AsyncFilesClient(self)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def backend(self) -> str:
        return self._backend

    async def __aenter__(self) -> "AsyncClient":
        if self._backend == "hosted":
            self._client = httpx.AsyncClient(timeout=self._timeout)
        elif self._ctxfs_client is not None:
            await self._ctxfs_client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._ctxfs_client is not None:
            await self._ctxfs_client.__aexit__(exc_type, exc, tb)

    async def search(
        self,
        query: str,
        *,
        prefix: str = "",
        limit: int | None = None,
    ) -> SearchResult:
        if self._ctxfs_client is not None:
            matches = await self._ctxfs_client.grep(query, prefix=prefix, limit=limit)
            return SearchResult(
                results=[
                    SearchItem(
                        id=f"{match.path}:{match.line_number}",
                        app_name="ctxfs",
                        title=match.path,
                        url=match.path,
                        text=match.line,
                        metadata={
                            "line_number": match.line_number,
                            "match_start": match.match_start,
                            "match_end": match.match_end,
                            "content_hash": match.content_hash,
                        },
                    )
                    for match in matches.items
                ],
                complete=matches.complete,
                stopped_by=matches.stopped_by,
            )

        if prefix or limit is not None:
            raise ValueError(
                "`prefix` and `limit` are only supported by the ctxfs backend."
            )
        payload = await self.call_tool("search", {"query": query})
        return SearchResult.model_validate(payload)

    async def fetch_document(self, document_uid: str) -> DocumentResult:
        if self._ctxfs_client is not None:
            document = await self._ctxfs_client.read(document_uid)
            return DocumentResult(
                id=document.path,
                app_name="ctxfs",
                title=document.path,
                url=document.path,
                text=document.text,
                metadata={
                    "content_hash": document.content_hash,
                    "complete": document.complete,
                    "stopped_by": document.stopped_by,
                },
            )

        payload = await self.call_tool("fetch_document", {"document_uid": document_uid})
        return DocumentResult.model_validate(payload)

    async def fetch(self, identifier: str) -> DocumentResult:
        return await self.fetch_document(identifier)

    async def get_profile(self) -> ProfileResult:
        if self._ctxfs_client is not None:
            status = await self._ctxfs_client.status()
            return ProfileResult(
                integration_access=(
                    "# Local ctxfs\n"
                    f"- Status: {status.status or 'unknown'}\n"
                    f"- Endpoint: {status.endpoint or self._ctxfs_client.endpoint}"
                ),
                file_tree="",
            )

        payload = await self.call_tool("get_profile", {})
        return ProfileResult.model_validate(payload)

    async def profile(self) -> ProfileResult:
        return await self.get_profile()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._backend != "hosted":
            raise CtxdError("MCP tools are only available for the hosted backend.")

        request_body = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
            "id": 1,
        }
        token = await self._resolve_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "User-Agent": get_user_agent(),
        }

        try:
            if self._client is not None:
                response = await self._client.post(
                    self._base_url,
                    headers=headers,
                    json=request_body,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._base_url,
                        headers=headers,
                        json=request_body,
                    )
        except httpx.RequestError as exc:
            raise CtxdError(
                f"Could not connect to ctxd at {self._base_url}. "
                "Check your internet connection and try again."
            ) from exc

        return self._parse_response(response)

    async def _resolve_access_token(self) -> str:
        if self._api_key:
            return self._api_key

        raise CtxdAuthError(
            "Missing API key. Set `CTXD_API_KEY`, run `ctxd login`, or pass `api_key=`."
        )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/sse"):
            normalized = normalized[: -len("/sse")]
        if not normalized.endswith("/mcp"):
            normalized = f"{normalized}/mcp"
        return normalized

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            message = f"ctxd MCP request failed with status {response.status_code}"
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = response.text
            raise CtxdError(
                message, status_code=response.status_code, payload=error_payload
            )

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return AsyncClient._parse_sse_payload(response.text)
        if "application/json" in content_type:
            return AsyncClient._parse_json_payload(response.json())

        if response.text.startswith("event:") or response.text.startswith("data:"):
            return AsyncClient._parse_sse_payload(response.text)

        raise CtxdProtocolError(
            f"Unsupported MCP response content type: {content_type or 'unknown'}"
        )

    @staticmethod
    def _parse_sse_payload(raw_text: str) -> dict[str, Any]:
        data_line = next(
            (line for line in raw_text.splitlines() if line.startswith("data: ")),
            None,
        )
        if data_line is None:
            raise CtxdProtocolError("MCP SSE response did not contain a data line")

        body = json.loads(data_line[len("data: ") :])
        return AsyncClient._parse_json_payload(body)

    @staticmethod
    def _parse_json_payload(body: dict[str, Any]) -> dict[str, Any]:
        if "error" in body:
            raise CtxdError("MCP JSON-RPC error", payload=body["error"])

        result = body.get("result")
        if not isinstance(result, dict):
            raise CtxdProtocolError("MCP response did not include a result object")

        content = result.get("content")
        if not isinstance(content, list) or not content:
            raise CtxdProtocolError("MCP result content was missing or empty")

        first_item = content[0]
        if first_item.get("type") != "text":
            raise CtxdProtocolError("MCP result content item was not text")

        text = first_item.get("text")
        if not isinstance(text, str):
            raise CtxdProtocolError("MCP result text payload was not a string")

        if result.get("isError"):
            raise CtxdError(text, payload=result)

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise CtxdProtocolError(
                "MCP result text payload was not valid JSON"
            ) from exc


class AsyncFilesClient:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def _ctxfs(self) -> AsyncCtxfsClient:
        if self._client._ctxfs_client is None:
            raise CtxdError("File operations are not supported by the hosted backend.")
        return self._client._ctxfs_client

    async def stat(self, path: str):
        return await self._ctxfs().stat(path)

    async def ls(self, path: str = "", *, limit: int | None = None):
        return await self._ctxfs().ls(path, limit=limit)

    async def tree(
        self,
        prefix: str = "",
        *,
        depth: int | None = None,
        limit: int | None = None,
    ):
        return await self._ctxfs().tree(prefix, depth=depth, limit=limit)

    async def glob(self, pattern: str, *, prefix: str = "", limit: int | None = None):
        return await self._ctxfs().glob(pattern, prefix=prefix, limit=limit)

    async def read(self, path: str, *, max_bytes: int | None = None):
        return await self._ctxfs().read(path, max_bytes=max_bytes)

    async def read_lines(self, path: str, *, start_line: int, end_line: int):
        return await self._ctxfs().read_lines(
            path,
            start_line=start_line,
            end_line=end_line,
        )
