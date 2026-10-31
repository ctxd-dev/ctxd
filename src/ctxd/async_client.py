from typing import Any
from ctxd.ctxfs_client import AsyncCtxfsClient


class AsyncClient:
    """Async client for ctxfs."""

    def __init__(
        self,
        *,
        ctxfs_endpoint: str | None = None,
        ctxfs_socket: str | None = None,
        ctxfs_api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._ctxfs_client = AsyncCtxfsClient(
            endpoint=ctxfs_endpoint,
            socket_path=ctxfs_socket,
            api_key=ctxfs_api_key,
            timeout=timeout,
        )
        self.files = AsyncFilesClient(self)

    async def __aenter__(self) -> "AsyncClient":
        await self._ctxfs_client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._ctxfs_client.__aexit__(exc_type, exc, tb)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._ctxfs_client.call_tool(name, arguments)


class AsyncFilesClient:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def _ctxfs(self) -> AsyncCtxfsClient:
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

    async def grep(
        self,
        pattern: str,
        *,
        prefix: str = "",
        limit: int | None = None,
        timeout_seconds: float | None = None,
    ):
        return await self._ctxfs().grep(
            pattern,
            prefix=prefix,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    async def read(self, path: str, *, max_bytes: int | None = None):
        return await self._ctxfs().read(path, max_bytes=max_bytes)

    async def read_lines(self, path: str, *, start_line: int, end_line: int):
        return await self._ctxfs().read_lines(
            path,
            start_line=start_line,
            end_line=end_line,
        )
