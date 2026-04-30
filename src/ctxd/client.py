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
        timeout: float = 30.0,
    ) -> None:
        self._async_client = AsyncClient(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    @property
    def base_url(self) -> str:
        return self._async_client.base_url

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def search(self, query: str) -> SearchResult:
        return self._run(self._async_client.search(query))

    def fetch_document(self, document_uid: str) -> DocumentResult:
        return self._run(self._async_client.fetch_document(document_uid))

    def get_profile(self) -> ProfileResult:
        return self._run(self._async_client.get_profile())

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
