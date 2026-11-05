from __future__ import annotations

import time
from dataclasses import dataclass

from ctxd.exceptions import CtxdIncompleteSearchError

DEFAULT_GREP_TIMEOUT_SECONDS = 2.0
MIN_GREP_TIMEOUT_SECONDS = 0.1


def _monotonic() -> float:
    return time.monotonic()


def incomplete_grep_error(stopped_by: str | None) -> CtxdIncompleteSearchError:
    reason = stopped_by or "an unknown safety bound"
    return CtxdIncompleteSearchError(
        f"ctxfs grep stopped because of {reason} and cannot provide an exhaustive "
        "result; narrow the prefix or pattern and retry"
    )


@dataclass(frozen=True)
class GrepTimeoutBudget:
    deadline: float

    @classmethod
    def start(cls, timeout_seconds: float | None) -> "GrepTimeoutBudget":
        effective_timeout = (
            DEFAULT_GREP_TIMEOUT_SECONDS
            if timeout_seconds is None
            else timeout_seconds
        )
        return cls(deadline=_monotonic() + effective_timeout)

    def remaining(self) -> float:
        remaining = self.deadline - _monotonic()
        if remaining < MIN_GREP_TIMEOUT_SECONDS:
            raise incomplete_grep_error("timeout")
        return remaining
