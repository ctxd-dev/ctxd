from typing import Any

import httpx

from ctxd._metadata import get_user_agent
from ctxd.config import resolve_ctxfs_endpoint
from ctxd.exceptions import CtxdError, CtxdProtocolError
from ctxd.models import (
    CtxfsBounded,
    CtxfsDirectoryEntry,
    CtxfsEntry,
    CtxfsGrepMatch,
    CtxfsReadLinesResult,
    CtxfsReadResult,
    CtxfsStatus,
)


class AsyncCtxfsClient:
    """Async client for the local ctxfs read interface."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        socket_path: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._endpoint = resolve_ctxfs_endpoint(url=endpoint, socket_path=socket_path)
        self._timeout = timeout
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

    async def read(
        self, path: str, *, max_bytes: int | None = None
    ) -> CtxfsReadResult:
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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"User-Agent": get_user_agent(), "Accept": "application/json"}
        try:
            if self._client is not None:
                response = await self._client.request(
                    method, path, params=params, headers=headers
                )
            else:
                async with self._build_client() as client:
                    response = await client.request(
                        method, path, params=params, headers=headers
                    )
        except httpx.RequestError as exc:
            raise CtxdError(
                f"Could not connect to ctxfs at {self._endpoint}. "
                "Check that the local ctxd service is running."
            ) from exc

        return self._parse_response(response)

    def _build_client(self) -> httpx.AsyncClient:
        if self._endpoint.startswith("unix://"):
            socket_path = self._endpoint.removeprefix("unix://")
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            return httpx.AsyncClient(
                base_url="http://ctxfs.local",
                timeout=self._timeout,
                transport=transport,
            )

        return httpx.AsyncClient(
            base_url=self._endpoint.rstrip("/"),
            timeout=self._timeout,
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


class CtxfsClient:
    """Synchronous client for the local ctxfs read interface."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        socket_path: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._async_client = AsyncCtxfsClient(
            endpoint=endpoint,
            socket_path=socket_path,
            timeout=timeout,
        )
        self.files = CtxfsFiles(self)

    @property
    def endpoint(self) -> str:
        return self._async_client.endpoint

    def status(self) -> CtxfsStatus:
        return _run(self._async_client.status())

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


def _run(coro):
    from ctxd.client import Client

    return Client._run(coro)


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
