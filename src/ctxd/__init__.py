"""Public Python SDK for ctxd."""

from ctxd._metadata import SDK_NAME, SDK_VERSION, get_user_agent
from ctxd.async_client import AsyncClient
from ctxd.client import Client, CtxdClient
from ctxd.config import (
    CtxfsEndpointResolution,
    get_config_path,
    load_config,
    resolve_ctxfs_endpoint,
    resolve_ctxfs_endpoint_details,
    save_config,
)
from ctxd.ctxfs_client import AsyncCtxfsClient, CtxfsClient
from ctxd.exceptions import (
    CtxdAuthError,
    CtxdError,
    CtxdIncompleteSearchError,
    CtxdProtocolError,
)
from ctxd.models import (
    CtxfsBounded,
    CtxfsDirectoryEntry,
    CtxfsEntry,
    CtxfsGrepMatch,
    CtxfsGrepPage,
    CtxfsGrepResult,
    CtxfsReadLinesResult,
    CtxfsReadResult,
    CtxfsStatus,
)

__all__ = [
    "AsyncClient",
    "AsyncCtxfsClient",
    "Client",
    "CtxfsBounded",
    "CtxfsClient",
    "CtxfsDirectoryEntry",
    "CtxfsEntry",
    "CtxfsEndpointResolution",
    "CtxfsGrepMatch",
    "CtxfsGrepPage",
    "CtxfsGrepResult",
    "CtxfsReadLinesResult",
    "CtxfsReadResult",
    "CtxfsStatus",
    "CtxdClient",
    "CtxdAuthError",
    "CtxdError",
    "CtxdIncompleteSearchError",
    "CtxdProtocolError",
    "SDK_NAME",
    "__version__",
    "get_config_path",
    "get_user_agent",
    "load_config",
    "resolve_ctxfs_endpoint",
    "resolve_ctxfs_endpoint_details",
    "save_config",
]
__version__ = SDK_VERSION
