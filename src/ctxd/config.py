import json
import os
import tempfile
from urllib.parse import urlsplit, urlunsplit
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ctxd.secure_store import (
    clear_ctxfs_api_key as clear_stored_ctxfs_api_key,
    clear_secret_bundle,
    load_ctxfs_api_key,
    load_secret_bundle,
    save_secret_bundle,
    save_ctxfs_api_key as save_stored_ctxfs_api_key,
)

DEFAULT_BASE_URL = "https://mcp.ctxd.dev"
DEFAULT_CONFIG_PATH = Path.home() / ".ctxd" / "config.json"
DEFAULT_BACKEND = "remote"
SUPPORTED_BACKENDS = frozenset({"hosted", "remote", "ctxfs"})
DEFAULT_CTXFS_SOCKET = Path.home() / ".ctxd" / "local" / "ctxfs.sock"


@dataclass(frozen=True)
class CtxfsEndpointResolution:
    """The effective ctxfs endpoint and who owns its process lifecycle."""

    endpoint: str
    source: str
    managed_local: bool


def get_config_path() -> Path:
    configured = os.getenv("CTXD_CONFIG_PATH")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CONFIG_PATH


def resolve_api_key(
    api_key: str | None = None, *, base_url: str | None = None
) -> str | None:
    if api_key and api_key.strip():
        return api_key.strip()

    env_api_key = os.getenv("CTXD_API_KEY")
    if env_api_key and env_api_key.strip():
        return env_api_key.strip()

    secret_bundle = load_secret_bundle(
        base_url=resolve_base_url(base_url), client_id=None
    )
    stored_api_key = secret_bundle.get("api_key")
    if isinstance(stored_api_key, str) and stored_api_key.strip():
        return stored_api_key.strip()

    return None


def load_config() -> dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict[str, Any]) -> Path:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f"{path.name}.", delete=False
    ) as tmp:
        tmp.write(json.dumps(config, indent=2, sort_keys=True) + "\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    os.replace(tmp_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def resolve_backend(backend: str | None = None) -> str:
    if backend and backend.strip():
        return _validate_backend(backend.strip())

    env_backend = os.getenv("CTXD_BACKEND")
    if env_backend and env_backend.strip():
        return _validate_backend(env_backend.strip())

    config_backend = load_config().get("backend")
    if isinstance(config_backend, str) and config_backend.strip():
        return _validate_backend(config_backend.strip())

    return DEFAULT_BACKEND


def save_backend(backend: str) -> Path:
    resolved_backend = _validate_backend(backend)
    config = load_config()
    config["backend"] = resolved_backend
    return save_config(config)


def save_ctxfs_endpoint(endpoint: str) -> Path:
    normalized = normalize_ctxfs_endpoint(endpoint)
    config = load_config()
    config.pop("ctxfs_socket", None)
    config["ctxfs_url"] = normalized
    return save_config(config)


def normalize_ctxfs_endpoint(endpoint: str) -> str:
    value = endpoint.strip()
    if not value:
        raise ValueError("ctxfs endpoint cannot be empty")
    if value.startswith("unix://"):
        return _socket_endpoint(value.removeprefix("unix://"))
    if "://" not in value:
        raise ValueError("ctxfs endpoint must be an HTTP(S) URL or unix:// socket")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("ctxfs endpoint must be an HTTP(S) URL or unix:// socket")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "ctxfs endpoint must not contain credentials, query, or fragment"
        )
    if parsed.path not in {"", "/"}:
        raise ValueError("ctxfs endpoint must not contain a path")
    return urlunsplit((parsed.scheme.lower(), host, "", "", ""))


def resolve_ctxfs_api_key(
    api_key: str | None = None, *, endpoint: str | None = None
) -> str | None:
    resolved_endpoint = normalize_ctxfs_endpoint(endpoint or resolve_ctxfs_endpoint())
    if resolved_endpoint.startswith("unix://"):
        return None
    if api_key and api_key.strip():
        return api_key.strip()
    env_api_key = os.getenv("CTXD_CTXFS_API_KEY")
    if env_api_key and env_api_key.strip():
        return env_api_key.strip()
    return load_ctxfs_api_key(endpoint=resolved_endpoint)


def save_ctxfs_api_key(api_key: str, *, endpoint: str | None = None) -> None:
    resolved_endpoint = normalize_ctxfs_endpoint(endpoint or resolve_ctxfs_endpoint())
    if resolved_endpoint.startswith("unix://"):
        raise ValueError("ctxfs API keys are not stored for trusted Unix sockets")
    save_stored_ctxfs_api_key(endpoint=resolved_endpoint, api_key=api_key)


def clear_ctxfs_api_key(*, endpoint: str | None = None) -> None:
    resolved_endpoint = normalize_ctxfs_endpoint(endpoint or resolve_ctxfs_endpoint())
    clear_stored_ctxfs_api_key(endpoint=resolved_endpoint)


def resolve_ctxfs_endpoint(
    *,
    url: str | None = None,
    socket_path: str | None = None,
) -> str:
    return resolve_ctxfs_endpoint_details(url=url, socket_path=socket_path).endpoint


def resolve_ctxfs_endpoint_details(
    *,
    url: str | None = None,
    socket_path: str | None = None,
) -> CtxfsEndpointResolution:
    if url and url.strip():
        return CtxfsEndpointResolution(
            normalize_ctxfs_endpoint(url), "argument-url", False
        )
    if socket_path and socket_path.strip():
        return CtxfsEndpointResolution(
            _socket_endpoint(socket_path.strip()), "argument-socket", False
        )

    env_url = os.getenv("CTXD_CTXFS_URL")
    if env_url and env_url.strip():
        return CtxfsEndpointResolution(
            normalize_ctxfs_endpoint(env_url), "environment-url", False
        )

    env_socket = os.getenv("CTXD_CTXFS_SOCKET")
    if env_socket and env_socket.strip():
        return CtxfsEndpointResolution(
            _socket_endpoint(env_socket.strip()), "environment-socket", False
        )

    config = load_config()
    config_url = config.get("ctxfs_url")
    if isinstance(config_url, str) and config_url.strip():
        return CtxfsEndpointResolution(
            normalize_ctxfs_endpoint(config_url), "config-url", False
        )

    config_socket = config.get("ctxfs_socket")
    if isinstance(config_socket, str) and config_socket.strip():
        return CtxfsEndpointResolution(
            _socket_endpoint(config_socket.strip()), "config-socket", False
        )

    daemon_endpoint = _resolve_local_daemon_ctxfs_endpoint()
    if daemon_endpoint:
        return CtxfsEndpointResolution(daemon_endpoint, "daemon-config", False)

    default_socket = DEFAULT_CTXFS_SOCKET.expanduser()
    return CtxfsEndpointResolution(
        _socket_endpoint(str(default_socket)), "default-managed-socket", True
    )


def save_api_key(api_key: str, *, base_url: str | None = None) -> Path:
    resolved_base_url = resolve_base_url(base_url)
    config = load_config()
    config["base_url"] = resolved_base_url
    bundle = load_secret_bundle(base_url=resolved_base_url, client_id=None)
    bundle["api_key"] = api_key.strip()
    save_secret_bundle(
        bundle,
        base_url=resolved_base_url,
        client_id=None,
    )
    return save_config(config)


def clear_api_key(*, base_url: str | None = None, keep_base_url: bool = True) -> Path:
    resolved_base_url = resolve_base_url(base_url)
    clear_secret_bundle(base_url=resolved_base_url, client_id=None)

    retained = load_config()
    if keep_base_url:
        retained["base_url"] = resolved_base_url
    else:
        retained.pop("base_url", None)

    return save_config(retained)


def resolve_base_url(base_url: str | None = None) -> str:
    if base_url and base_url.strip():
        return base_url.strip()

    env_base_url = os.getenv("CTXD_BASE_URL")
    if env_base_url and env_base_url.strip():
        return env_base_url.strip()

    config_base_url = load_config().get("base_url")
    if isinstance(config_base_url, str) and config_base_url.strip():
        return config_base_url.strip()

    return DEFAULT_BASE_URL


def _validate_backend(backend: str) -> str:
    normalized = backend.strip().lower()
    if normalized not in SUPPORTED_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise ValueError(
            f"Unsupported ctxd backend `{backend}`. Supported: {supported}."
        )
    if normalized == "hosted":
        return "remote"
    return normalized


def _socket_endpoint(socket_path: str) -> str:
    if socket_path.startswith("unix://"):
        return socket_path
    return f"unix://{Path(socket_path).expanduser()}"


def _resolve_local_daemon_ctxfs_endpoint() -> str | None:
    config_path = Path.home() / ".ctxd" / "local" / "config.toml"
    if not config_path.exists():
        return None

    try:
        import tomllib

        config = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None

    for key in ("ctxfs_url", "ctxfs_endpoint"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    value = config.get("ctxfs_socket")
    if isinstance(value, str) and value.strip():
        return _socket_endpoint(value.strip())

    ctxfs = config.get("ctxfs")
    if isinstance(ctxfs, dict):
        socket_path = ctxfs.get("socket_path")
        if isinstance(socket_path, str) and socket_path.strip():
            if socket_path.strip().lower() not in {"none", "null"}:
                return _socket_endpoint(socket_path.strip())

        host = ctxfs.get("host")
        port = ctxfs.get("port")
        if isinstance(host, str) and host.strip() and isinstance(port, int):
            return f"http://{host.strip()}:{port}"

    return None


def _resolve_base_url_from_config(config: dict[str, Any]) -> str:
    config_base_url = config.get("base_url")
    if isinstance(config_base_url, str) and config_base_url.strip():
        return config_base_url.strip()
    return DEFAULT_BASE_URL
