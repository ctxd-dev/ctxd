import json
from typing import Any

import httpx

from ctxd._metadata import get_user_agent
from ctxd.config import resolve_api_key, resolve_base_url
from ctxd.exceptions import CtxdAuthError, CtxdError, CtxdProtocolError
from ctxd.models import DocumentResult, ProfileResult, SearchResult


class AsyncClient:
    """Async client for the public ctxd MCP endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = self._normalize_base_url(resolve_base_url(base_url))
        self._api_key = resolve_api_key(api_key, base_url=self._base_url)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    async def __aenter__(self) -> "AsyncClient":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str) -> SearchResult:
        payload = await self.call_tool("search", {"query": query})
        return SearchResult.model_validate(payload)

    async def fetch_document(self, document_uid: str) -> DocumentResult:
        payload = await self.call_tool("fetch_document", {"document_uid": document_uid})
        return DocumentResult.model_validate(payload)

    async def get_profile(self) -> ProfileResult:
        payload = await self.call_tool("get_profile", {})
        return ProfileResult.model_validate(payload)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
