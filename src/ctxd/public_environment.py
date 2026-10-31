"""Registry and access helper for supported public ctxd environment variables."""

from __future__ import annotations

from enum import Enum
import os


class PublicEnvironmentVariable(str, Enum):
    """User-facing environment variables supported by the ctxd process."""

    CONFIG_PATH = "CTXD_CONFIG_PATH"
    CREDENTIALS_PATH = "CTXD_CREDENTIALS_PATH"
    CTXFS_API_KEY = "CTXD_CTXFS_API_KEY"
    CTXFS_ENROLLMENT_TOKEN = "CTXD_CTXFS_ENROLLMENT_TOKEN"
    CTXFS_SOCKET = "CTXD_CTXFS_SOCKET"
    CTXFS_URL = "CTXD_CTXFS_URL"


def getenv(variable: PublicEnvironmentVariable) -> str | None:
    """Read a registered public variable from the process environment."""

    if not isinstance(variable, PublicEnvironmentVariable):
        raise TypeError("variable must be a PublicEnvironmentVariable")
    return os.getenv(variable.value)
