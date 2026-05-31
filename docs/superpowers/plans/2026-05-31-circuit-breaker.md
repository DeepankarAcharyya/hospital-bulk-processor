# Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure-Python `CircuitBreaker` async context manager to `internal/clients/circuit_breaker.py` and wire two independent instances into the bulk processing loop in `server.py`.

**Architecture:** `CircuitBreaker` is a plain class with `__aenter__`/`__aexit__` that tracks state (`CLOSED → OPEN → HALF_OPEN → CLOSED`) using a failure counter and a monotonic timestamp. Two module-level instances (`create_breaker`, `activate_breaker`) live in `server.py` and persist across requests. `CircuitOpenError` is raised on fast-fail and carries `retry_after` so the caller can sleep.

**Tech Stack:** Python 3.11, `httpx` (failure signal), `time.monotonic` (timeout tracking), `pytest-asyncio` (asyncio_mode=auto, no decorators needed), `unittest.mock.patch` (time mocking)

---

## Task 1: `CircuitOpenError` + `CircuitBreaker` skeleton (CLOSED state)

**Files:**
- Create: `internal/clients/circuit_breaker.py`
- Create: `tests/unit/test_circuit_breaker.py`

- [ ] **Step 1: Write failing tests for `CircuitOpenError` and CLOSED-state behaviour**

```python
# tests/unit/test_circuit_breaker.py
import pytest
import httpx

from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError


class TestCircuitOpenError:
    def test_stores_retry_after(self):
        err = CircuitOpenError(retry_after=30.0)
        assert err.retry_after == 30.0

    def test_is_exception(self):
        assert isinstance(CircuitOpenError(retry_after=0.0), Exception)


class TestCircuitBreakerClosed:
    async def test_initial_state_is_closed(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        assert breaker.state == "closed"

    async def test_successful_call_stays_closed(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        async with breaker:
            pass
        assert breaker.state == "closed"

    async def test_failure_below_threshold_stays_closed(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("timeout")
        assert breaker.state == "closed"

    async def test_failures_at_threshold_open_circuit(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("timeout")
        assert breaker.state == "open"

    async def test_aexit_does_not_suppress_exceptions(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("real error")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_circuit_breaker.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — module does not exist yet.

- [ ] **Step 3: Implement `CircuitOpenError` and `CircuitBreaker` (CLOSED state only)**

```python
# internal/clients/circuit_breaker.py
from __future__ import annotations

import time
from enum import Enum

import httpx


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Circuit open. Retry after {retry_after:.1f}s")


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
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None and issubclass(exc_type, httpx.HTTPError):
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._failure_threshold:
                self._state = _State.OPEN
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_circuit_breaker.py::TestCircuitOpenError \
       tests/unit/test_circuit_breaker.py::TestCircuitBreakerClosed -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/clients/circuit_breaker.py tests/unit/test_circuit_breaker.py
git commit -m "feat: add CircuitBreaker skeleton with CLOSED state logic"
```

---

## Task 2: OPEN state — fast-fail and `retry_after`

**Files:**
- Modify: `internal/clients/circuit_breaker.py`
- Modify: `tests/unit/test_circuit_breaker.py`

- [ ] **Step 1: Add failing tests for OPEN state**

Append to `tests/unit/test_circuit_breaker.py`:

```python
from unittest.mock import patch


class TestCircuitBreakerOpen:
    def _make_open_breaker(self) -> CircuitBreaker:
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        # trip it
        with pytest.raises(httpx.ConnectError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(self._fail(breaker))
        return breaker

    async def _fail(self, breaker: CircuitBreaker) -> None:
        async with breaker:
            raise httpx.ConnectError("down")

    async def test_open_raises_circuit_open_error(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("down")
        assert breaker.state == "open"
        with pytest.raises(CircuitOpenError):
            async with breaker:
                pass  # should never reach here

    async def test_open_does_not_execute_body(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("down")
        executed = False
        with pytest.raises(CircuitOpenError):
            async with breaker:
                executed = True
        assert not executed

    async def test_retry_after_is_remaining_timeout(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 110.0  # 10s elapsed
            with pytest.raises(CircuitOpenError) as exc_info:
                async with breaker:
                    pass
        assert exc_info.value.retry_after == pytest.approx(20.0)  # 30 - 10

    async def test_retry_after_never_negative(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 29.9  # just under timeout, still OPEN
            with pytest.raises(CircuitOpenError) as exc_info:
                async with breaker:
                    pass
        assert exc_info.value.retry_after >= 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_circuit_breaker.py::TestCircuitBreakerOpen -v
```

Expected: `FAILED` — `__aenter__` doesn't yet raise `CircuitOpenError`.

- [ ] **Step 3: Implement OPEN state in `__aenter__`**

Replace the `__aenter__` method in `internal/clients/circuit_breaker.py`:

```python
async def __aenter__(self) -> None:
    if self._state == _State.OPEN:
        elapsed = time.monotonic() - (self._last_failure_time or 0.0)
        if elapsed >= self._recovery_timeout:
            self._state = _State.HALF_OPEN
        else:
            retry_after = max(0.0, self._recovery_timeout - elapsed)
            raise CircuitOpenError(retry_after)
    return None
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
pytest tests/unit/test_circuit_breaker.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/clients/circuit_breaker.py tests/unit/test_circuit_breaker.py
git commit -m "feat: implement OPEN state fast-fail with retry_after"
```

---

## Task 3: HALF_OPEN state — probe, recover, reopen

**Files:**
- Modify: `internal/clients/circuit_breaker.py`
- Modify: `tests/unit/test_circuit_breaker.py`

- [ ] **Step 1: Add failing tests for HALF_OPEN state**

Append to `tests/unit/test_circuit_breaker.py`:

```python
class TestCircuitBreakerHalfOpen:
    async def test_transitions_to_half_open_after_timeout(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            assert breaker.state == "open"
            mock_time.monotonic.return_value = 31.0  # past recovery_timeout
            async with breaker:
                assert breaker.state == "half_open"

    async def test_half_open_success_closes_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            async with breaker:
                pass  # probe succeeds
        assert breaker.state == "closed"

    async def test_half_open_success_resets_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            for _ in range(2):
                with pytest.raises(httpx.ConnectError):
                    async with breaker:
                        raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            async with breaker:
                pass  # probe succeeds → CLOSED, count reset
        # now one failure should NOT reopen (threshold=2, count reset to 0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("one failure")
        assert breaker.state == "closed"

    async def test_half_open_failure_reopens_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("still down")  # probe fails
        assert breaker.state == "open"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_circuit_breaker.py::TestCircuitBreakerHalfOpen -v
```

Expected: `FAILED` — `__aexit__` doesn't yet handle HALF_OPEN transitions.

- [ ] **Step 3: Implement HALF_OPEN handling in `__aexit__`**

Replace the `__aexit__` method in `internal/clients/circuit_breaker.py`:

```python
async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
    if exc_type is not None and issubclass(exc_type, httpx.HTTPError):
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
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
pytest tests/unit/test_circuit_breaker.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/clients/circuit_breaker.py tests/unit/test_circuit_breaker.py
git commit -m "feat: implement HALF_OPEN probe with close-on-success and reopen-on-failure"
```

---

## Task 4: Integrate into `server.py`

**Files:**
- Modify: `server.py` (lines 1-14 imports, lines 75-103 bulk loop)

- [ ] **Step 1: Add imports and module-level breaker instances**

In `server.py`, add `asyncio` to the stdlib imports at the top and the circuit breaker import after the existing internal imports:

```python
import asyncio   # add after: import os
```

After the existing `from internal.clients.hospital_client import ...` line, add:

```python
from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
```

After `log = structlog.get_logger(__name__)`, add:

```python
create_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
activate_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
```

- [ ] **Step 2: Replace the per-row try/except block**

Find the existing per-row block (currently lines ~76–93 in `server.py`):

```python
            try:
                result = await create_hospital(client, hospital, batch_id, row)
                results.append(result)
            except httpx.HTTPError as e:
                failed += 1
                log.error(
                    "hospital_create_failed",
                    row=row,
                    name=hospital.name,
                    error=repr(e),
                )
                results.append(HospitalResult(
                    row=row,
                    hospital_id=0,
                    name=hospital.name,
                    status="failed",
                ))
```

Replace with:

```python
            try:
                async with create_breaker:
                    result = await create_hospital(client, hospital, batch_id, row)
                results.append(result)
            except CircuitOpenError as e:
                failed += 1
                log.warning(
                    "create_circuit_open",
                    row=row,
                    name=hospital.name,
                    retry_after=e.retry_after,
                )
                await asyncio.sleep(e.retry_after)
                results.append(HospitalResult(
                    row=row,
                    hospital_id=0,
                    name=hospital.name,
                    status="failed",
                ))
            except httpx.HTTPError as e:
                failed += 1
                log.error(
                    "hospital_create_failed",
                    row=row,
                    name=hospital.name,
                    error=repr(e),
                )
                results.append(HospitalResult(
                    row=row,
                    hospital_id=0,
                    name=hospital.name,
                    status="failed",
                ))
```

- [ ] **Step 3: Replace the batch activation try/except block**

Find the existing activation block (currently lines ~95–102 in `server.py`):

```python
        batch_activated = False
        if failed == 0:
            try:
                batch_activated = await activate_batch(client, batch_id)
                # Mark all results as created_and_activated
                results = [r.model_copy(update={"status": "created_and_activated"}) for r in results]
            except httpx.HTTPStatusError:
                log.error("batch_activation_failed", batch_id=str(batch_id))
```

Replace with:

```python
        batch_activated = False
        if failed == 0:
            try:
                async with activate_breaker:
                    batch_activated = await activate_batch(client, batch_id)
                results = [r.model_copy(update={"status": "created_and_activated"}) for r in results]
            except CircuitOpenError as e:
                log.error(
                    "activate_circuit_open",
                    batch_id=str(batch_id),
                    retry_after=e.retry_after,
                )
            except httpx.HTTPStatusError:
                log.error("batch_activation_failed", batch_id=str(batch_id))
```

- [ ] **Step 4: Run the full test suite**

```bash
pytest -v
```

Expected: all existing tests PASS. No regressions.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat: wire circuit breakers into bulk processing loop"
```
