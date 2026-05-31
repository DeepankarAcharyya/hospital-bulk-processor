# Resilient Bulk Hospital Processing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple bulk processing from the HTTP thread via `asyncio.Queue` + background worker, add per-row exponential retry with a pure-Python circuit breaker, and expose a `202 Accepted` + progress-polling API.

**Architecture:** `POST /hospitals/bulk` validates, seeds in-memory state, enqueues the job, returns `202`. A single background worker (started via FastAPI `lifespan`) pulls jobs and processes rows with retry+backoff through two isolated `CircuitBreaker` context managers (`create_breaker`, `activate_breaker`). An `asyncio.Lock` serialises downstream HTTP calls. `GET /hospitals/batch/{id}/progress` polls live state.

**Tech Stack:** Python 3.11, FastAPI, httpx, asyncio (stdlib), pydantic, structlog, pytest-asyncio (asyncio_mode=auto), unittest.mock.patch

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `internal/models/batch.py` | `BatchStatus` enum, `BatchState` pydantic model |
| Modify | `internal/store/in_memory.py` | `InMemoryStore` — dict-backed state store |
| Create | `internal/clients/circuit_breaker.py` | `CircuitOpenError`, `CircuitBreaker` state machine |
| Create | `tests/unit/test_circuit_breaker.py` | Unit tests for all CB states |
| Create | `tests/unit/test_in_memory_store.py` | Unit tests for store |
| Modify | `server.py` | Queue, lock, worker, lifespan, 202 endpoint, progress endpoint |
| Create | `tests/integration/test_progress_endpoint.py` | Integration test for progress polling |

---

## Task 1: `BatchStatus` and `BatchState` models

**Files:**
- Create: `internal/models/batch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_batch_models.py
import pytest
from internal.models.batch import BatchState, BatchStatus


def test_batch_state_defaults():
    state = BatchState(batch_id="abc", status=BatchStatus.ACCEPTED, total_hospitals=5)
    assert state.processed_hospitals == 0
    assert state.failed_hospitals == 0
    assert state.batch_activated is False
    assert state.error_message is None


def test_batch_status_values():
    assert BatchStatus.ACCEPTED == "accepted"
    assert BatchStatus.PROCESSING == "processing"
    assert BatchStatus.COOLDOWN == "cooldown"
    assert BatchStatus.COMPLETED == "completed"
    assert BatchStatus.FAILED == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_batch_models.py -v
```

Expected: `ModuleNotFoundError` — module does not exist yet.

- [ ] **Step 3: Implement `BatchStatus` and `BatchState`**

```python
# internal/models/batch.py
from __future__ import annotations

from enum import Enum

import pydantic


class BatchStatus(str, Enum):
    ACCEPTED   = "accepted"
    PROCESSING = "processing"
    COOLDOWN   = "cooldown"
    COMPLETED  = "completed"
    FAILED     = "failed"


class BatchState(pydantic.BaseModel):
    batch_id: str
    status: BatchStatus
    total_hospitals: int
    processed_hospitals: int = 0
    failed_hospitals: int = 0
    batch_activated: bool = False
    error_message: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_batch_models.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/models/batch.py tests/unit/test_batch_models.py
git commit -m "feat: add BatchStatus and BatchState models"
```

---

## Task 2: `InMemoryStore`

**Files:**
- Modify: `internal/store/in_memory.py`
- Create: `tests/unit/test_in_memory_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_in_memory_store.py
import pytest
from internal.models.batch import BatchState, BatchStatus
from internal.store.in_memory import InMemoryStore


def _state(batch_id: str = "batch-1") -> BatchState:
    return BatchState(batch_id=batch_id, status=BatchStatus.ACCEPTED, total_hospitals=3)


def test_get_unknown_returns_none():
    store = InMemoryStore()
    assert store.get("unknown") is None


def test_set_then_get_returns_state():
    store = InMemoryStore()
    state = _state()
    store.set("batch-1", state)
    assert store.get("batch-1") == state


def test_update_partial_fields():
    store = InMemoryStore()
    store.set("batch-1", _state())
    store.update("batch-1", status=BatchStatus.PROCESSING, processed_hospitals=2)
    result = store.get("batch-1")
    assert result.status == BatchStatus.PROCESSING
    assert result.processed_hospitals == 2
    assert result.total_hospitals == 3  # unchanged


def test_update_unknown_key_raises():
    store = InMemoryStore()
    with pytest.raises(KeyError):
        store.update("nonexistent", status=BatchStatus.FAILED)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_in_memory_store.py -v
```

Expected: `ImportError` or `AttributeError` — store is currently empty stubs.

- [ ] **Step 3: Implement `InMemoryStore`**

```python
# internal/store/in_memory.py
from __future__ import annotations

from internal.models.batch import BatchState


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, BatchState] = {}

    def set(self, batch_id: str, state: BatchState) -> None:
        self._data[batch_id] = state

    def get(self, batch_id: str) -> BatchState | None:
        return self._data.get(batch_id)

    def update(self, batch_id: str, **kwargs) -> None:
        state = self._data[batch_id]  # raises KeyError if missing
        self._data[batch_id] = state.model_copy(update=kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_in_memory_store.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/store/in_memory.py tests/unit/test_in_memory_store.py
git commit -m "feat: implement InMemoryStore for batch state tracking"
```

---

## Task 3: `CircuitBreaker` — skeleton and CLOSED state

**Files:**
- Create: `internal/clients/circuit_breaker.py`
- Create: `tests/unit/test_circuit_breaker.py`

- [ ] **Step 1: Write failing tests**

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

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement skeleton + CLOSED state**

```python
# internal/clients/circuit_breaker.py
from __future__ import annotations

import time
from enum import Enum

import httpx


class _State(Enum):
    CLOSED   = "closed"
    OPEN     = "open"
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

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/clients/circuit_breaker.py tests/unit/test_circuit_breaker.py
git commit -m "feat: add CircuitBreaker skeleton with CLOSED state logic"
```

---

## Task 4: `CircuitBreaker` — OPEN state

**Files:**
- Modify: `internal/clients/circuit_breaker.py`
- Modify: `tests/unit/test_circuit_breaker.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_circuit_breaker.py`**

```python
from unittest.mock import patch


class TestCircuitBreakerOpen:
    async def test_open_raises_circuit_open_error(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("down")
        assert breaker.state == "open"
        with pytest.raises(CircuitOpenError):
            async with breaker:
                pass

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
            mock_time.monotonic.return_value = 29.9
            with pytest.raises(CircuitOpenError) as exc_info:
                async with breaker:
                    pass
        assert exc_info.value.retry_after >= 0.0
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
pytest tests/unit/test_circuit_breaker.py::TestCircuitBreakerOpen -v
```

Expected: `FAILED` — `__aenter__` doesn't yet raise `CircuitOpenError`.

- [ ] **Step 3: Implement OPEN state in `__aenter__`**

Replace `__aenter__` in `internal/clients/circuit_breaker.py`:

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

- [ ] **Step 4: Run all circuit breaker tests to verify they pass**

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

## Task 5: `CircuitBreaker` — HALF_OPEN state

**Files:**
- Modify: `internal/clients/circuit_breaker.py`
- Modify: `tests/unit/test_circuit_breaker.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_circuit_breaker.py`**

```python
class TestCircuitBreakerHalfOpen:
    async def test_transitions_to_half_open_after_timeout(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
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
                pass
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
                pass  # probe succeeds → CLOSED, count reset to 0
        # one more failure should NOT reopen (threshold=2, count was reset)
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
                    raise httpx.ConnectError("still down")
        assert breaker.state == "open"
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
pytest tests/unit/test_circuit_breaker.py::TestCircuitBreakerHalfOpen -v
```

Expected: `FAILED` — `__aexit__` doesn't yet handle HALF_OPEN transitions.

- [ ] **Step 3: Implement HALF_OPEN handling in `__aexit__`**

Replace `__aexit__` in `internal/clients/circuit_breaker.py`:

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

- [ ] **Step 4: Run all circuit breaker tests to verify they pass**

```bash
pytest tests/unit/test_circuit_breaker.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/clients/circuit_breaker.py tests/unit/test_circuit_breaker.py
git commit -m "feat: implement HALF_OPEN probe — close on success, reopen on failure"
```

---

## Task 6: Background worker with retry + exponential backoff

**Files:**
- Modify: `server.py` (add worker function; do NOT wire into server yet — that's Task 7)
- Create: `tests/unit/test_worker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_worker.py
import asyncio
import pytest
import httpx
import respx
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from internal.models.batch import BatchState, BatchStatus
from internal.store.in_memory import InMemoryStore
from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
from internal.models.hospital import Hospital


def _make_hospital(name: str = "Test Hospital") -> Hospital:
    return Hospital(name=name, address="1 Main St", city="NYC", state="NY", zip_code="10001")


async def _run_worker_once(store, hospitals, batch_id=None):
    """Helper: run process_batch_job directly (bypasses queue)."""
    from server import process_batch_job
    bid = str(batch_id or uuid4())
    store.set(bid, BatchState(batch_id=bid, status=BatchStatus.ACCEPTED, total_hospitals=len(hospitals)))
    create_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
    activate_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
    api_lock = asyncio.Lock()
    await process_batch_job(bid, hospitals, store, create_breaker, activate_breaker, api_lock)
    return bid


@respx.mock
async def test_happy_path_marks_completed(respx_mock):
    respx_mock.post("/hospitals/").mock(return_value=httpx.Response(201, json={"id": 1}))
    respx_mock.patch(httpx.URL, url__regex=r"/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    store = InMemoryStore()
    hospitals = [_make_hospital(f"H{i}") for i in range(3)]
    bid = await _run_worker_once(store, hospitals)
    state = store.get(bid)
    assert state.status == BatchStatus.COMPLETED
    assert state.processed_hospitals == 3
    assert state.batch_activated is True


@respx.mock
async def test_row_failure_exhausts_retries_marks_failed(respx_mock):
    respx_mock.post("/hospitals/").mock(side_effect=httpx.ConnectError("down"))
    store = InMemoryStore()
    bid = await _run_worker_once(store, [_make_hospital()])
    state = store.get(bid)
    assert state.failed_hospitals == 1
    assert state.batch_activated is False


@respx.mock
async def test_retries_six_times_then_continues(respx_mock):
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 6:
            raise httpx.ConnectError("down")
        return httpx.Response(201, json={"id": 1})

    respx_mock.post("/hospitals/").mock(side_effect=side_effect)
    respx_mock.patch(httpx.URL, url__regex=r"/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    store = InMemoryStore()
    # Two hospitals: first exhausts 6 retries (fails), second succeeds on attempt 7 overall
    # but reset per-row, so second row has fresh attempt counter
    hospitals = [_make_hospital("fail"), _make_hospital("ok")]

    # Actually: call_count is shared. Row 1 gets 6 calls (all fail). Row 2 gets call 7 (succeeds).
    bid = await _run_worker_once(store, hospitals)
    state = store.get(bid)
    assert state.failed_hospitals == 1
    assert state.processed_hospitals == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_worker.py -v
```

Expected: `ImportError` — `process_batch_job` not in `server.py` yet.

- [ ] **Step 3: Add `process_batch_job` to `server.py`**

Add the following function to `server.py` (do NOT replace the existing endpoint yet):

```python
import asyncio

from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
from internal.models.batch import BatchState, BatchStatus
from internal.store.in_memory import InMemoryStore


async def process_batch_job(
    batch_id: str,
    hospitals: list,
    store: InMemoryStore,
    create_breaker: CircuitBreaker,
    activate_breaker: CircuitBreaker,
    api_lock: asyncio.Lock,
) -> None:
    store.update(batch_id, status=BatchStatus.PROCESSING)
    failed = 0
    results = []

    async with httpx.AsyncClient(base_url=HOSPITALS_API_URL, timeout=30.0) as client:
        for row, hospital in enumerate(hospitals, start=1):
            attempt = 0
            success = False
            while attempt < 6:
                try:
                    async with create_breaker:
                        async with api_lock:
                            result = await create_hospital(client, hospital, batch_id_as_uuid(batch_id), row)
                    results.append(result)
                    success = True
                    break
                except CircuitOpenError as e:
                    store.update(batch_id, status=BatchStatus.COOLDOWN)
                    log.warning("create_circuit_open", batch_id=batch_id, row=row, attempt=attempt, retry_after=e.retry_after)
                    await asyncio.sleep(e.retry_after)
                    attempt += 1
                except httpx.HTTPError as e:
                    backoff = min(60.0, 2.0 ** attempt)
                    log.warning("create_failed_retry", batch_id=batch_id, row=row, attempt=attempt, backoff=backoff, error=repr(e))
                    await asyncio.sleep(backoff)
                    attempt += 1

            if not success:
                failed += 1
                log.error("create_exhausted_retries", batch_id=batch_id, row=row, name=hospital.name)
                results.append(HospitalResult(row=row, hospital_id=0, name=hospital.name, status="failed"))

            store.update(
                batch_id,
                status=BatchStatus.PROCESSING,
                processed_hospitals=row - failed,
                failed_hospitals=failed,
            )

        batch_activated = False
        if failed == 0:
            try:
                async with activate_breaker:
                    async with api_lock:
                        batch_activated = await activate_batch(client, uuid.UUID(batch_id))
                results = [r.model_copy(update={"status": "created_and_activated"}) for r in results]
            except CircuitOpenError as e:
                log.error("activate_circuit_open", batch_id=batch_id, retry_after=e.retry_after)
                store.update(batch_id, status=BatchStatus.FAILED, error_message="Activation circuit open")
                return
            except httpx.HTTPStatusError:
                log.error("batch_activation_failed", batch_id=batch_id)
                store.update(batch_id, status=BatchStatus.FAILED, error_message="Activation HTTP error")
                return

    final_status = BatchStatus.COMPLETED if failed == 0 else BatchStatus.FAILED
    store.update(batch_id, status=final_status, batch_activated=batch_activated)
    log.info("batch_complete", batch_id=batch_id, failed=failed, activated=batch_activated)
```

Also add this helper near the top of `server.py` (after imports):

```python
import uuid as _uuid_module

def batch_id_as_uuid(batch_id: str) -> _uuid_module.UUID:
    return _uuid_module.UUID(batch_id)
```

- [ ] **Step 4: Run worker tests to verify they pass**

```bash
pytest tests/unit/test_worker.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest -v
```

Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_worker.py
git commit -m "feat: add process_batch_job worker with retry and exponential backoff"
```

---

## Task 7: Server integration — queue, lifespan, 202 endpoint, progress endpoint

**Files:**
- Modify: `server.py`
- Create: `tests/integration/test_progress_endpoint.py`

- [ ] **Step 1: Write failing integration tests**

```python
# tests/integration/test_progress_endpoint.py
import pytest
import httpx
from fastapi.testclient import TestClient


def test_bulk_returns_202_with_batch_id(tmp_path):
    from server import app
    client = TestClient(app)
    csv_content = b"name,address,city,state,zip_code\nGeneral Hospital,1 Main St,NYC,NY,10001\n"
    response = client.post(
        "/hospitals/bulk",
        files={"file": ("hospitals.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 202
    data = response.json()
    assert "batch_id" in data


def test_progress_returns_404_for_unknown_batch():
    from server import app
    client = TestClient(app)
    response = client.get("/hospitals/batch/nonexistent-id/progress")
    assert response.status_code == 404


def test_progress_returns_state_for_known_batch(tmp_path):
    from server import app, store
    from internal.models.batch import BatchState, BatchStatus
    store.set("test-batch", BatchState(batch_id="test-batch", status=BatchStatus.PROCESSING, total_hospitals=5))
    client = TestClient(app)
    response = client.get("/hospitals/batch/test-batch/progress")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert data["total_hospitals"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_progress_endpoint.py -v
```

Expected: `FAILED` — `POST /hospitals/bulk` still returns `200`, progress endpoint doesn't exist.

- [ ] **Step 3: Add singletons and lifespan to `server.py`**

At the top of `server.py`, after existing imports, add:

```python
import asyncio

from contextlib import asynccontextmanager

from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
from internal.models.batch import BatchState, BatchStatus
from internal.store.in_memory import InMemoryStore
```

Replace `app = FastAPI()` with:

```python
job_queue      = asyncio.Queue()
api_lock       = asyncio.Lock()
create_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
activate_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
store          = InMemoryStore()


async def _worker_loop() -> None:
    while True:
        batch_id, hospitals = await job_queue.get()
        try:
            await process_batch_job(batch_id, hospitals, store, create_breaker, activate_breaker, api_lock)
        except Exception as exc:
            log.error("worker_unhandled_error", batch_id=batch_id, error=repr(exc))
            try:
                store.update(batch_id, status=BatchStatus.FAILED, error_message=repr(exc))
            except KeyError:
                pass
        finally:
            job_queue.task_done()


@asynccontextmanager
async def lifespan(app_: FastAPI):
    task = asyncio.create_task(_worker_loop())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan)
```

- [ ] **Step 4: Replace `POST /hospitals/bulk` to return `202`**

Replace the existing `bulk_create_hospitals` endpoint with:

```python
@app.post("/hospitals/bulk", status_code=202)
@limiter.limit("10/minute")
async def bulk_create_hospitals(request: Request, file: UploadFile = File(...)):
    if file.content_type not in ("text/csv", "application/csv"):
        log.warning("invalid_content_type", content_type=file.content_type)
        raise HTTPException(status_code=400, detail="File must be a CSV")

    content = await file.read()

    try:
        hospitals = parse_and_validate_csv(content)
    except CSVValidationError as e:
        log.warning("csv_validation_failed", error_count=len(e.errors), errors=e.errors)
        return JSONResponse(status_code=422, content={"errors": e.errors})

    batch_id = str(uuid.uuid4())
    store.set(batch_id, BatchState(
        batch_id=batch_id,
        status=BatchStatus.ACCEPTED,
        total_hospitals=len(hospitals),
    ))
    await job_queue.put((batch_id, hospitals))
    log.info("batch_enqueued", batch_id=batch_id, total=len(hospitals))

    return {"batch_id": batch_id}
```

- [ ] **Step 5: Add `GET /hospitals/batch/{batch_id}/progress` endpoint**

Add after the bulk endpoint:

```python
@app.get("/hospitals/batch/{batch_id}/progress")
async def get_batch_progress(batch_id: str):
    state = store.get(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return state
```

- [ ] **Step 6: Run full test suite**

```bash
pytest -v
```

Expected: all tests PASS. The old integration tests that expected a `200` response with `hospitals` array will need to be updated — see next step.

- [ ] **Step 7: Update existing integration tests**

Open `tests/integration/test_bulk_endpoint.py`. The endpoint now returns `202 { batch_id }` instead of `200 { hospitals: [...] }`. Update any assertions checking `status_code == 200` → `status_code == 202`, and update body assertions to check `"batch_id" in response.json()` instead of checking `hospitals`.

- [ ] **Step 8: Run full test suite again**

```bash
pytest -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add server.py tests/integration/test_progress_endpoint.py tests/integration/test_bulk_endpoint.py
git commit -m "feat: async queue + lifespan worker + 202 endpoint + progress polling"
```
