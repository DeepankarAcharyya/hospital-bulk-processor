from __future__ import annotations

import time
from enum import Enum

import httpx


class _State(Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Circuit open. Retry after {retry_after:.1f}s")


_TRANSIENT_CODES: frozenset[int] = frozenset({429, 503})


def _is_transient(exc_type: type, exc_val: BaseException) -> bool:
    if issubclass(exc_type, httpx.TransportError):
        return True
    if issubclass(exc_type, httpx.HTTPStatusError):
        return exc_val.response.status_code in _TRANSIENT_CODES
    return False


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._state = _State.CLOSED

    @property
    def state(self) -> str:
        return self._state.value

    async def __aenter__(self) -> None:
        if self._state == _State.OPEN:
            assert self._last_failure_time is not None
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = _State.HALF_OPEN
            else:
                retry_after = max(0.0, self._recovery_timeout - elapsed)
                raise CircuitOpenError(retry_after)
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None and _is_transient(exc_type, exc_val):
            self._last_failure_time = time.monotonic()
            if self._state == _State.HALF_OPEN:
                self._state = _State.OPEN
            else:  # CLOSED
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._state = _State.OPEN
        elif exc_type is None and self._state == _State.HALF_OPEN:
            self._state = _State.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
        return False
