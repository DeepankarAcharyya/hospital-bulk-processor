# Circuit Breaker Design

**Date:** 2026-05-31  
**Scope:** `internal/clients/circuit_breaker.py` + integration into `server.py`

---

## Problem

`hospital_client.py` calls an external HTTP API with no resilience logic. A flaky or down
downstream service causes every row in a bulk batch to wait for a timeout before failing.
The circuit breaker pattern detects repeated failures and fails fast, reducing latency and
load on the downstream service during outages.

---

## Architecture

### State Machine

```
CLOSED ──(failures >= threshold)──► OPEN ──(recovery_timeout elapsed)──► HALF_OPEN
  ▲                                                                            │
  └──────────────────(probe success)──────────────────────────────────────────┘
  (probe failure returns to OPEN, resets timer)
```

| State     | Behaviour                                                               |
|-----------|-------------------------------------------------------------------------|
| CLOSED    | All calls pass through. Failure counter increments on each `httpx.HTTPError`. |
| OPEN      | Fail fast: raise `CircuitOpenError` immediately. No call made.         |
| HALF_OPEN | One probe call allowed. Success → CLOSED + reset counter. Failure → OPEN + reset timer. |

**Transitions triggered by:** `__aenter__` (OPEN → HALF_OPEN check) and `__aexit__` (record outcome).

### New File

`internal/clients/circuit_breaker.py` — standalone module, no imports from the rest of this
package. Pure stdlib (`time`, `enum`).

---

## Components

### `CircuitOpenError`

```python
class CircuitOpenError(Exception):
    retry_after: float  # seconds until breaker may transition to HALF_OPEN
```

`retry_after = max(0.0, recovery_timeout - elapsed_since_last_failure)`

### `CircuitBreaker`

```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0): ...
    async def __aenter__(self) -> None: ...       # raises CircuitOpenError when OPEN
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool: ...  # always returns False
    @property
    def state(self) -> str: ...                   # "closed" | "open" | "half_open"
```

`__aexit__` counts any `httpx.HTTPError` subclass as a failure. All other exceptions
(including `CircuitOpenError`) are ignored by the breaker — they propagate unchanged.
`__aexit__` never suppresses exceptions (always returns `False`).

**Thread/async safety:** state transitions are synchronous with no `await` points inside
`__aenter__`/`__aexit__`, so no `asyncio.Lock` is required under the GIL.

---

## Integration in `server.py`

Two module-level instances (persist across requests, accumulate state over the process lifetime):

```python
from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError

create_breaker   = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
activate_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
```

`activate_breaker` uses a lower threshold and shorter timeout because `activate_batch` is
called once per batch (not per row), so a single failure is more significant.

### Bulk loop (per-row)

```python
try:
    async with create_breaker:
        result = await create_hospital(client, hospital, batch_id, row)
    results.append(result)
except CircuitOpenError as e:
    failed += 1
    log.warning("create_circuit_open", row=row, retry_after=e.retry_after)
    await asyncio.sleep(e.retry_after)
    results.append(HospitalResult(row=row, hospital_id=0, name=hospital.name, status="failed"))
except httpx.HTTPError as e:
    failed += 1
    log.error("hospital_create_failed", row=row, name=hospital.name, error=repr(e))
    results.append(HospitalResult(row=row, hospital_id=0, name=hospital.name, status="failed"))
```

Loop continues after `CircuitOpenError` — the breaker fast-fails all remaining rows at
negligible cost after sleeping `retry_after` once.

### Batch activation

```python
if failed == 0:
    try:
        async with activate_breaker:
            batch_activated = await activate_batch(client, batch_id)
    except CircuitOpenError as e:
        log.error("activate_circuit_open", batch_id=str(batch_id), retry_after=e.retry_after)
    except httpx.HTTPStatusError:
        log.error("batch_activation_failed", batch_id=str(batch_id))
```

---

## Error Handling

| Exception         | Source              | Handler                                      |
|-------------------|---------------------|----------------------------------------------|
| `CircuitOpenError`| breaker OPEN        | log warning, sleep `retry_after`, mark failed |
| `httpx.HTTPError` | real network/HTTP   | log error, mark failed (existing behaviour)  |
| Any other         | passes through      | unhandled, propagates up                     |

---

## Out of Scope

- Per-status-code filtering (all `httpx.HTTPError` counts as failure, including 4xx)
- Persistent state across process restarts
- Metrics/instrumentation hooks
- Retry logic (separate concern)
