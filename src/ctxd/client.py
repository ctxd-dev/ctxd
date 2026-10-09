import asyncio
import threading
from typing import Any

from ctxd.async_client import AsyncClient
from ctxd.models import DocumentResult, ProfileResult, SearchResult


class Client:
    """Synchronous client for the public ctxd MCP endpoint."""

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
        self._async_client = AsyncClient(
            api_key=api_key,
            base_url=base_url,
            backend=backend,
            ctxfs_endpoint=ctxfs_endpoint,
            ctxfs_socket=ctxfs_socket,
            timeout=timeout,
        )
        self.files = FilesClient(self)

    @property
    def base_url(self) -> str:
        return self._async_client.base_url

    @property
    def backend(self) -> str:
        return self._async_client.backend

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def search(
        self,
        query: str,
        *,
        prefix: str = "",
        limit: int | None = None,
    ) -> SearchResult:
        return self._run(self._async_client.search(query, prefix=prefix, limit=limit))

    def fetch_document(self, document_uid: str) -> DocumentResult:
        return self._run(self._async_client.fetch_document(document_uid))

    def fetch(self, identifier: str) -> DocumentResult:
        return self.fetch_document(identifier)

    def get_profile(self) -> ProfileResult:
        return self._run(self._async_client.get_profile())

    def profile(self) -> ProfileResult:
        return self.get_profile()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._run(self._async_client.call_tool(name, arguments))

    @staticmethod
    def _run(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except BaseException as exc:  # pragma: no cover - forwarded to caller
                error["value"] = exc

        thread = threading.Thread(target=runner)
        thread.start()
        thread.join()

        if "value" in error:
            raise error["value"]
        return result["value"]


CtxdClient = Client


class FilesClient:
    def __init__(self, client: Client) -> None:
        self._client = client

    def stat(self, path: str):
        return self._client._run(self._client._async_client.files.stat(path))

    def ls(self, path: str = "", *, limit: int | None = None):
        return self._client._run(
            self._client._async_client.files.ls(path, limit=limit)
        )

    def tree(
        self,
        prefix: str = "",
        *,
        depth: int | None = None,
        limit: int | None = None,
    ):
        return self._client._run(
            self._client._async_client.files.tree(
                prefix,
                depth=depth,
                limit=limit,
            )
        )

    def glob(self, pattern: str, *, prefix: str = "", limit: int | None = None):
        return self._client._run(
            self._client._async_client.files.glob(
                pattern,
                prefix=prefix,
                limit=limit,
            )
        )

    def read(self, path: str, *, max_bytes: int | None = None):
        return self._client._run(
            self._client._async_client.files.read(path, max_bytes=max_bytes)
        )

    def read_lines(self, path: str, *, start_line: int, end_line: int):
        return self._client._run(
            self._client._async_client.files.read_lines(
                path,
                start_line=start_line,
                end_line=end_line,
            )
        )
