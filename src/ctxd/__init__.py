"""Public Python SDK for ctxd."""

from ctxd._metadata import SDK_NAME, SDK_VERSION, get_user_agent
from ctxd.async_client import AsyncClient
from ctxd.client import Client, CtxdClient
from ctxd.config import (
    DEFAULT_BASE_URL,
    clear_api_key,
    get_config_path,
    load_config,
    resolve_api_key,
    save_api_key,
    save_config,
)
from ctxd.exceptions import CtxdAuthError, CtxdError, CtxdProtocolError
from ctxd.models import DocumentResult, ProfileResult, SearchResult

__all__ = [
    "AsyncClient",
    "Client",
    "CtxdClient",
    "CtxdAuthError",
    "CtxdError",
    "CtxdProtocolError",
    "DEFAULT_BASE_URL",
    "DocumentResult",
    "ProfileResult",
    "SDK_NAME",
    "SearchResult",
    "__version__",
    "clear_api_key",
    "get_config_path",
    "get_user_agent",
    "load_config",
    "resolve_api_key",
    "save_config",
    "save_api_key",
]
__version__ = SDK_VERSION
