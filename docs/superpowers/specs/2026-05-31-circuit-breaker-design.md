# Resilient Bulk Hospital Processing — Architecture Design

**Date:** 2026-05-31  
**Scope:** Full async queue architecture with circuit breakers, retry/backoff, progress polling

---

## Problem

The current `POST /hospitals/bulk` endpoint processes rows synchronously, blocks the HTTP thread for the full batch duration, has no resilience against downstream failures, and provides no progress visibility. Under rate limiting or downstream outages, the entire batch fails with no recovery path.

---

## Architecture Overview

Decouple the HTTP request from batch processing using an in-memory producer-consumer topology.

```
[Client]
   │  POST /hospitals/bulk (CSV)
   ▼
[FastAPI Router] ── validate CSV ──► [InMemoryStore] ── seed BatchState
   │                                                      (status=accepted)
   │  enqueue batch job
   ▼
[asyncio.Queue]
   │  pulled by background worker
   ▼
[Background Worker]
   │
   ├─► for each row:
   │     async with create_breaker:         ← CircuitBreaker #1
   │       async with api_lock:             ← asyncio.Lock
   │         POST /hospitals/
   │     [retry w/ exponential backoff, max 6 attempts]
   │
   └─► all rows done:
         async with activate_breaker:       ← CircuitBreaker #2
           PATCH /hospitals/batch/{id}/activate
```

**`POST /hospitals/bulk`** returns `202 Accepted` immediately with `batch_id`.  
**`GET /hospitals/batch/{batch_id}/progress`** reads live state from `InMemoryStore`.

---

## Components

### 1. `BatchStatus` / `BatchState` — `internal/models/batch.py`

```python
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

### 2. `InMemoryStore` — `internal/store/in_memory.py`

Dict-backed store, single instance, shared across app and worker.

```python
class InMemoryStore:
    def set(self, batch_id: str, state: BatchState) -> None
    def get(self, batch_id: str) -> BatchState | None
    def update(self, batch_id: str, **kwargs) -> None   # partial update
```

### 3. `CircuitBreaker` — `internal/clients/circuit_breaker.py`

Pure Python async context manager. No new dependencies.

```
CLOSED ──(failures >= threshold)──► OPEN ──(recovery_timeout elapsed)──► HALF_OPEN
  ▲                                                                             │
  └──────────────(probe success)───────────────────────────────────────────────┘
  (probe failure → OPEN, reset timer)
```

| State     | Behaviour |
|-----------|-----------|
| CLOSED    | Pass through; count `httpx.HTTPError` failures |
| OPEN      | Raise `CircuitOpenError(retry_after)` immediately; no call made |
| HALF_OPEN | Allow one probe; success → CLOSED + reset count; failure → OPEN + reset timer |

```python
class CircuitOpenError(Exception):
    retry_after: float   # max(0.0, recovery_timeout - elapsed_since_last_failure)

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0)
    async def __aenter__(self) -> None      # raises CircuitOpenError when OPEN
    async def __aexit__(self, ...) -> bool  # always False; counts httpx.HTTPError
    @property
    def state(self) -> str                  # "closed" | "open" | "half_open"
```

`__aexit__` never suppresses exceptions. Only `httpx.HTTPError` subclasses count as failures.

### 4. Background Worker — `server.py`

Single long-running coroutine, launched via FastAPI `lifespan`. Pulls `BatchJob` from the queue and processes it.

**Per-row retry logic (max 6 attempts, exponential backoff):**

```python
attempt = 0
while attempt < 6:
    try:
        async with create_breaker:
            async with api_lock:
                result = await create_hospital(client, hospital, batch_id, row)
        # success: break
        break
    except CircuitOpenError as e:
        store.update(batch_id, status=BatchStatus.COOLDOWN)
        await asyncio.sleep(e.retry_after)
        attempt += 1
    except httpx.HTTPError:
        backoff = min(60.0, 2.0 ** attempt)   # 1, 2, 4, 8, 16, 32 s
        await asyncio.sleep(backoff)
        attempt += 1
else:
    # all 6 attempts exhausted — mark row failed, continue to next row
```

Both `CircuitOpenError` and `httpx.HTTPError` consume an attempt. After 6 failures, row is marked failed; worker continues to the next row without re-raising.

**Activation step:** only runs if `failed == 0`. Wrapped in `activate_breaker`. `CircuitOpenError` and `httpx.HTTPStatusError` both mark batch as `FAILED` (no retry — activation is once-per-batch).

### 5. Module-level singletons — `server.py`

```python
job_queue     = asyncio.Queue()
api_lock      = asyncio.Lock()
create_breaker   = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
activate_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
store            = InMemoryStore()
```

All persist for the process lifetime (survive across requests). Worker is started once in `lifespan`.

### 6. API changes — `server.py`

**`POST /hospitals/bulk`** (modified):
- Validates CSV (unchanged)
- Seeds `BatchState(status=ACCEPTED)` in store
- Enqueues `(batch_id, hospitals)` onto `job_queue`
- Returns `202 Accepted` with `{ "batch_id": "..." }`

**`GET /hospitals/batch/{batch_id}/progress`** (new):
- Reads `BatchState` from store
- Returns 404 if unknown, 200 with state otherwise

---

## Data Flow — Happy Path

1. Client uploads 20-row CSV → `POST /hospitals/bulk`
2. Server validates, seeds store, enqueues, returns `202 { batch_id }`
3. Worker pulls job, sets status → `PROCESSING`
4. For each row: acquires `create_breaker`, acquires `api_lock`, calls `POST /hospitals/`
5. All rows succeed; worker calls `PATCH /.../activate` via `activate_breaker`
6. Store → `COMPLETED`, `batch_activated=True`
7. Client polls `GET /hospitals/batch/{id}/progress` → sees `completed`

## Resilience — Downstream Failure During Row Loop

1. `httpx.HTTPError` on row N → exponential backoff (1s, 2s, 4s…), retry up to 6×
2. After 6 failures: row marked failed, status → `cooldown`, continue to row N+1
3. If `failure_count >= threshold` (5): `create_breaker` trips to `OPEN`
4. Subsequent rows get `CircuitOpenError` → sleep `retry_after`, then probe (HALF_OPEN)
5. If all rows fail: activation skipped, batch → `FAILED`

---

## Trade-offs

| Factor | In-Memory Async | Redis + Celery |
|--------|-----------------|----------------|
| Infrastructure | Zero (stdlib only) | Multi-container |
| Durability | Lost on restart | Persisted |
| Scalability | Single-node only | Horizontal |
| Fit for ≤20 rows | Optimal | Over-engineered |

Horizontal scaling would break `asyncio.Lock` (per-process). If needed later: replace lock with distributed rate limiter, queue with Redis Streams.

---

## Out of Scope

- Persistent queue across restarts
- Per-status-code filtering (all `httpx.HTTPError` counts as failure)
- Metrics/instrumentation hooks
- Multiple concurrent workers
