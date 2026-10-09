"""Public Python SDK for ctxd."""

from ctxd._metadata import SDK_NAME, SDK_VERSION, get_user_agent
from ctxd.async_client import AsyncClient
from ctxd.client import Client, CtxdClient
from ctxd.config import (
    DEFAULT_BASE_URL,
    DEFAULT_BACKEND,
    clear_api_key,
    get_config_path,
    load_config,
    resolve_api_key,
    resolve_backend,
    resolve_ctxfs_endpoint,
    save_api_key,
    save_backend,
    save_config,
)
from ctxd.ctxfs_client import AsyncCtxfsClient, CtxfsClient
from ctxd.exceptions import CtxdAuthError, CtxdError, CtxdProtocolError
from ctxd.models import (
    CtxfsBounded,
    CtxfsDirectoryEntry,
    CtxfsEntry,
    CtxfsGrepMatch,
    CtxfsReadLinesResult,
    CtxfsReadResult,
    CtxfsStatus,
    DocumentResult,
    ProfileResult,
    SearchResult,
)

__all__ = [
    "AsyncClient",
    "AsyncCtxfsClient",
    "Client",
    "CtxfsBounded",
    "CtxfsClient",
    "CtxfsDirectoryEntry",
    "CtxfsEntry",
    "CtxfsGrepMatch",
    "CtxfsReadLinesResult",
    "CtxfsReadResult",
    "CtxfsStatus",
    "CtxdClient",
    "CtxdAuthError",
    "CtxdError",
    "CtxdProtocolError",
    "DEFAULT_BASE_URL",
    "DEFAULT_BACKEND",
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
    "resolve_backend",
    "resolve_ctxfs_endpoint",
    "save_config",
    "save_api_key",
    "save_backend",
]
__version__ = SDK_VERSION
