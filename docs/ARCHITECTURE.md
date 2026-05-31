# Architecture

Hospital Bulk Processor is a single-process FastAPI application. It accepts CSV uploads, stores batch progress in memory, and uses one background worker to call the upstream hospital directory API.

## Runtime Components

```text
Client
  |
  | POST /hospitals/bulk
  v
FastAPI API layer
  |
  | parse and validate CSV
  v
InMemoryStore + asyncio.Queue
  |
  | background worker
  v
Hospital Directory API
  |
  | POST /hospitals/
  | PATCH /hospitals/batch/{batch_id}/activate
  v
Activated hospital records
```

## Main Files

- `server.py`: FastAPI app, request logging middleware, rate limiting, batch queue, background worker, upload endpoint, and progress endpoint.
- `internal/validation/csv.py`: CSV parsing, header validation, row validation, and maximum row enforcement.
- `internal/models/hospital.py`: Pydantic model for hospital records.
- `internal/models/batch.py`: Batch status and progress state model.
- `internal/models/bulk_response.py`: Legacy response/result models returned by the hospital client helper. The current worker does not retain per-row result objects in batch state.
- `internal/clients/hospital_client.py`: HTTP client helpers for the upstream hospital API.
- `internal/clients/circuit_breaker.py`: Circuit breaker implementation for transient downstream failures.
- `internal/store/in_memory.py`: In-memory batch progress store.
- `internal/logging_config.py`: JSON logging setup with `structlog`.
- `test_client/client.py`: Example command-line client for uploading a CSV.

## API Layer

The API layer exposes three endpoints:

- `GET /`: simple health check.
- `POST /hospitals/bulk`: accepts a CSV file, validates it, creates a `batch_id`, stores initial progress, enqueues the job, and returns `202 Accepted`.
- `GET /hospitals/batch/{batch_id}/progress`: returns the current batch state from the in-memory store.

The upload endpoint is rate limited to `10/minute` per remote address through `slowapi`.

## Background Worker

The worker is started from FastAPI lifespan startup. It runs continuously and consumes `(batch_id, hospitals)` items from an `asyncio.Queue`.

For each queued batch, the worker:

1. Marks the batch as `processing`.
2. Creates each hospital through the upstream API.
3. Updates aggregate progress counters after each row.
4. Activates the upstream batch if all creates succeed.
5. Marks the batch as `completed` or `failed`.

Only one worker loop is defined, so batch processing is serialized inside the process.

The performance-optimized worker does not store per-hospital results. It tracks aggregate progress through `processed_hospitals`, `failed_hospitals`, `status`, `batch_activated`, and `error_message`.

## Resilience

The service uses two circuit breakers:

- Create circuit breaker: protects `POST /hospitals/`.
- Activation circuit breaker: protects `PATCH /hospitals/batch/{batch_id}/activate`.

Transient conditions include upstream transport failures and selected HTTP status codes such as `429` and `503`. When a circuit is open, the worker moves the batch into `cooldown` and waits before retrying.

An `asyncio.Lock` serializes upstream API calls. This avoids sending concurrent create or activation requests from this process.

## State Model

Batch state is stored in memory:

```json
{
  "batch_id": "uuid-string",
  "status": "accepted",
  "total_hospitals": 5,
  "processed_hospitals": 0,
  "failed_hospitals": 0,
  "batch_activated": false,
  "error_message": null
}
```

Because the store is in memory, progress state is not durable across process restarts or redeploys.

## Deployment Shape

The application is containerized with the root-level `Dockerfile`. Render builds the repository root, installs dependencies with `uv`, and runs:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Public URL:

```text
https://hospital-bulk-processor-6466.onrender.com
```
