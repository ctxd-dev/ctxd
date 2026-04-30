from typing import Any


class CtxdError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class CtxdProtocolError(CtxdError):
    """Raised when the MCP server response does not match the expected shape."""


class CtxdAuthError(CtxdError):
    """Raised when login, refresh, or credential resolution fails."""
