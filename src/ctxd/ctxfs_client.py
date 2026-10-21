import asyncio
from typing import Any

import httpx

from ctxd._metadata import get_user_agent
from ctxd.config import resolve_ctxfs_api_key, resolve_ctxfs_endpoint_details
from ctxd.exceptions import CtxdError, CtxdProtocolError
from ctxd.models import (
    CtxfsBounded,
    CtxfsDirectoryEntry,
    CtxfsEntry,
    CtxfsGrepMatch,
    CtxfsMachineRegistration,
    CtxfsReadLinesResult,
    CtxfsReadResult,
    CtxfsStatus,
)

_USE_CLIENT_API_KEY = object()


class AsyncCtxfsClient:
    """Async client for the local ctxfs read interface."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        socket_path: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        resolution = resolve_ctxfs_endpoint_details(
            url=endpoint, socket_path=socket_path
        )
        self._endpoint = resolution.endpoint
        self._managed_local = resolution.managed_local
        self._managed_local_ready = False
        self._timeout = timeout
        self._api_key = resolve_ctxfs_api_key(api_key, endpoint=self._endpoint)
        self._client: httpx.AsyncClient | None = None

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def __aenter__(self) -> "AsyncCtxfsClient":
        self._client = self._build_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def status(self) -> CtxfsStatus:
        payload = await self._request("GET", "/health")
        if "status" not in payload:
            payload["status"] = "ok"
        payload.setdefault("endpoint", self._endpoint)
        return CtxfsStatus.model_validate(payload)

    async def validate_authentication(self) -> None:
        """Validate a bearer token without requiring the status capability.

        A valid credential that lacks ``status`` receives 403 from ``/health``;
        an absent or invalid credential receives 401 from authentication
        middleware. This keeps submit-only tracker credentials usable while
        still rejecting invalid tokens before they are persisted or a service
        is started.
        """
        try:
            await self.status()
        except CtxdError as exc:
            if exc.status_code != 403:
                raise

    async def register_machine(
        self,
        *,
        local_registration_id: str,
        name: str,
        description: str | None,
        platform: str | None,
        ctxd_version: str | None,
        enrollment_token: str | None,
    ) -> CtxfsMachineRegistration:
        response_status: list[int] = []
        payload = await self._request(
            "POST",
            "/api/ctxfs/machines/register",
            json={
                "local_registration_id": local_registration_id,
                "name": name,
                "description": description,
                "platform": platform,
                "ctxd_version": ctxd_version,
            },
            bearer_token=enrollment_token,
            response_status=response_status,
        )
        if response_status not in ([200], [201]):
            raise CtxdProtocolError(
                "ctxfs registration response had an unexpected HTTP status"
            )
        registration = CtxfsMachineRegistration.model_validate(payload)
        return registration.model_copy(update={"recovered": response_status == [200]})

    async def stat(self, path: str) -> CtxfsEntry:
        payload = await self._request("GET", "/api/ctxfs/stat", params={"path": path})
        return CtxfsEntry.model_validate(payload)

    async def ls(
        self, path: str = "", *, limit: int | None = None
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        params = _drop_none({"path": path, "limit": limit})
        payload = await self._request("GET", "/api/ctxfs/ls", params=params)
        return CtxfsBounded[CtxfsDirectoryEntry].model_validate(payload)

    async def tree(
        self,
        prefix: str = "",
        *,
        depth: int | None = None,
        limit: int | None = None,
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        params = _drop_none({"prefix": prefix, "depth": depth, "limit": limit})
        payload = await self._request("GET", "/api/ctxfs/tree", params=params)
        return CtxfsBounded[CtxfsDirectoryEntry].model_validate(payload)

    async def glob(
        self,
        pattern: str,
        *,
        prefix: str = "",
        limit: int | None = None,
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        params = _drop_none({"pattern": pattern, "prefix": prefix, "limit": limit})
        payload = await self._request("GET", "/api/ctxfs/glob", params=params)
        return CtxfsBounded[CtxfsDirectoryEntry].model_validate(payload)

    async def read(self, path: str, *, max_bytes: int | None = None) -> CtxfsReadResult:
        params = _drop_none({"path": path, "max_bytes": max_bytes})
        payload = await self._request("GET", "/api/ctxfs/read", params=params)
        return CtxfsReadResult.model_validate(payload)

    async def read_lines(
        self, path: str, *, start_line: int, end_line: int
    ) -> CtxfsReadLinesResult:
        params = {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
        }
        payload = await self._request("GET", "/api/ctxfs/read-lines", params=params)
        return CtxfsReadLinesResult.model_validate(payload)

    async def grep(
        self,
        pattern: str,
        *,
        prefix: str = "",
        limit: int | None = None,
    ) -> CtxfsBounded[CtxfsGrepMatch]:
        params = _drop_none({"pattern": pattern, "prefix": prefix, "limit": limit})
        payload = await self._request("GET", "/api/ctxfs/grep", params=params)
        return CtxfsBounded[CtxfsGrepMatch].model_validate(payload)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
                "id": 1,
            },
        )
        if "error" in payload:
            raise CtxdProtocolError(f"ctxfs MCP request failed: {payload['error']}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise CtxdProtocolError("ctxfs MCP response did not include a result")
        return result

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        bearer_token: str | None | object = _USE_CLIENT_API_KEY,
        response_status: list[int] | None = None,
    ) -> dict[str, Any]:
        if self._managed_local and not self._managed_local_ready:
            await asyncio.to_thread(_ensure_managed_local_started)
            self._managed_local_ready = True

        headers = {"User-Agent": get_user_agent(), "Accept": "application/json"}
        token = self._api_key if bearer_token is _USE_CLIENT_API_KEY else bearer_token
        if token and not self._endpoint.startswith("unix://"):
            headers["Authorization"] = f"Bearer {token}"
        request_kwargs: dict[str, Any] = {"params": params, "headers": headers}
        if json is not None:
            request_kwargs["json"] = json
        try:
            if self._client is not None:
                response = await self._client.request(method, path, **request_kwargs)
            else:
                async with self._build_client() as client:
                    response = await client.request(method, path, **request_kwargs)
        except httpx.RequestError as exc:
            raise CtxdError(
                f"Could not connect to ctxfs at {self._endpoint}. "
                "Check that the local ctxd service is running."
            ) from exc

        if response_status is not None:
            response_status.append(response.status_code)
        return self._parse_response(response)

    def _build_client(self) -> httpx.AsyncClient:
        if self._endpoint.startswith("unix://"):
            socket_path = self._endpoint.removeprefix("unix://")
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            return httpx.AsyncClient(
                base_url="http://ctxfs.local",
                timeout=self._timeout,
                transport=transport,
                follow_redirects=False,
            )

        return httpx.AsyncClient(
            base_url=self._endpoint.rstrip("/"),
            timeout=self._timeout,
            follow_redirects=False,
        )

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            message = _error_message(response.status_code)
            try:
                payload: Any = response.json()
            except ValueError:
                payload = response.text
            raise CtxdError(message, status_code=response.status_code, payload=payload)

        try:
            payload = response.json()
        except ValueError as exc:
            raise CtxdProtocolError("ctxfs response was not valid JSON") from exc

        if not isinstance(payload, dict):
            raise CtxdProtocolError("ctxfs response did not include a JSON object")
        return payload


class CtxfsFiles:
    def __init__(self, client: "CtxfsClient") -> None:
        self._client = client

    def stat(self, path: str) -> CtxfsEntry:
        return self._client.stat(path)

    def ls(
        self, path: str = "", *, limit: int | None = None
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        return self._client.ls(path, limit=limit)

    def tree(
        self,
        prefix: str = "",
        *,
        depth: int | None = None,
        limit: int | None = None,
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        return self._client.tree(prefix, depth=depth, limit=limit)

    def glob(
        self, pattern: str, *, prefix: str = "", limit: int | None = None
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        return self._client.glob(pattern, prefix=prefix, limit=limit)

    def read(self, path: str, *, max_bytes: int | None = None) -> CtxfsReadResult:
        return self._client.read(path, max_bytes=max_bytes)

    def read_lines(
        self, path: str, *, start_line: int, end_line: int
    ) -> CtxfsReadLinesResult:
        return self._client.read_lines(path, start_line=start_line, end_line=end_line)

    def grep(
        self, pattern: str, *, prefix: str = "", limit: int | None = None
    ) -> CtxfsBounded[CtxfsGrepMatch]:
        return self._client.grep(pattern, prefix=prefix, limit=limit)


class CtxfsClient:
    """Synchronous client for the local ctxfs read interface."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        socket_path: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._async_client = AsyncCtxfsClient(
            endpoint=endpoint,
            socket_path=socket_path,
            api_key=api_key,
            timeout=timeout,
        )
        self.files = CtxfsFiles(self)

    @property
    def endpoint(self) -> str:
        return self._async_client.endpoint

    def status(self) -> CtxfsStatus:
        return _run(self._async_client.status())

    def validate_authentication(self) -> None:
        return _run(self._async_client.validate_authentication())

    def register_machine(
        self,
        *,
        local_registration_id: str,
        name: str,
        description: str | None,
        platform: str | None,
        ctxd_version: str | None,
        enrollment_token: str | None,
    ) -> CtxfsMachineRegistration:
        return _run(
            self._async_client.register_machine(
                local_registration_id=local_registration_id,
                name=name,
                description=description,
                platform=platform,
                ctxd_version=ctxd_version,
                enrollment_token=enrollment_token,
            )
        )

    def stat(self, path: str) -> CtxfsEntry:
        return _run(self._async_client.stat(path))

    def ls(
        self, path: str = "", *, limit: int | None = None
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        return _run(self._async_client.ls(path, limit=limit))

    def tree(
        self,
        prefix: str = "",
        *,
        depth: int | None = None,
        limit: int | None = None,
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        return _run(self._async_client.tree(prefix, depth=depth, limit=limit))

    def glob(
        self, pattern: str, *, prefix: str = "", limit: int | None = None
    ) -> CtxfsBounded[CtxfsDirectoryEntry]:
        return _run(self._async_client.glob(pattern, prefix=prefix, limit=limit))

    def read(self, path: str, *, max_bytes: int | None = None) -> CtxfsReadResult:
        return _run(self._async_client.read(path, max_bytes=max_bytes))

    def read_lines(
        self, path: str, *, start_line: int, end_line: int
    ) -> CtxfsReadLinesResult:
        return _run(
            self._async_client.read_lines(
                path,
                start_line=start_line,
                end_line=end_line,
            )
        )

    def grep(
        self, pattern: str, *, prefix: str = "", limit: int | None = None
    ) -> CtxfsBounded[CtxfsGrepMatch]:
        return _run(self._async_client.grep(pattern, prefix=prefix, limit=limit))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return _run(self._async_client.call_tool(name, arguments))


def _run(coro):
    from ctxd.client import Client

    return Client._run(coro)


def _ensure_managed_local_started() -> None:
    # Imported only when the SDK owns the selected endpoint. The service module
    # uses this client for its own explicit-socket health checks, so importing it
    # at module load time would create a cycle.
    from ctxd.local_ctxfs_service import ensure_started

    ensure_started()


def _drop_none(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


def _error_message(status_code: int) -> str:
    if status_code == 404:
        return "ctxfs path was not found"
    if status_code == 409:
        return "ctxfs document is unavailable"
    if status_code == 503:
        return "ctxfs service is unavailable"
    if status_code in {400, 422}:
        return "ctxfs request was invalid"
    return f"ctxfs request failed with status {status_code}"
