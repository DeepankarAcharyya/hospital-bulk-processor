# hospital-bulk-processor

Hospital Bulk Processor is a FastAPI service for accepting small CSV batches of hospital records, validating them, and processing them asynchronously against a hospital directory backend.

Deployed service:

```text
https://hospital-bulk-processor-6466.onrender.com
```

Upstream hospital directory API:

```text
https://hospital-directory.onrender.com
```

## What It Does

- Accepts CSV uploads at `POST /hospitals/bulk`.
- Validates required hospital fields before processing.
- Enqueues accepted batches and returns a `batch_id` immediately.
- Processes rows in a background worker.
- Retries transient upstream failures with backoff.
- Uses circuit breakers for create and activation calls.
- Exposes batch progress at `GET /hospitals/batch/{batch_id}/progress`.
- Tracks aggregate batch progress plus per-row hospital results.
- Activates the upstream batch only after all rows are created successfully.
- Includes a sample client that can upload new CSVs, poll an existing batch ID, or resume a failed batch.

## API Summary

### Health Check

```http
GET /
```

Example response:

```json
{
  "message": "Hello World"
}
```

### Upload Hospital CSV

```http
POST /hospitals/bulk
Content-Type: multipart/form-data
```

The multipart field name must be `file`.

Example accepted response:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2",
  "status": "accepted",
  "total_hospitals": 2,
  "processed_hospitals": 0,
  "failed_hospitals": 0,
  "processing_time_seconds": 0,
  "batch_activated": false,
  "hospitals": [
    {
      "row": 1,
      "hospital_id": null,
      "name": "General Hospital",
      "status": "accepted"
    }
  ],
  "error_message": null
}
```

The endpoint returns `202 Accepted` when the CSV is valid and the batch is queued. The response uses the same batch state shape as the progress endpoint. At this point `processing_time_seconds` is `0` because the worker has not started recording elapsed processing time yet.

### Resume Failed Batch

```http
POST /hospitals/batch/{batch_id}/resume
```

If the batch is already `completed`, the endpoint returns `200 OK`.

If the batch is `failed`, the endpoint resets aggregate progress, requeues the saved parsed hospital rows, and returns `202 Accepted`.

Unknown batch IDs return `404 Not Found`. Active batches return `409 Conflict`.

### Check Batch Progress

```http
GET /hospitals/batch/{batch_id}/progress
```

Example response:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2",
  "status": "processing",
  "total_hospitals": 5,
  "processed_hospitals": 3,
  "failed_hospitals": 0,
  "processing_time_seconds": 42.7,
  "batch_activated": false,
  "hospitals": [
    {
      "row": 1,
      "hospital_id": 101,
      "name": "General Hospital",
      "status": "created"
    }
  ],
  "error_message": null
}
```

`processing_time_seconds` is the last elapsed worker time saved in `InMemoryStore`. It starts at `0`, is updated while the worker processes rows and activation, and is reset to `0` when a failed batch is accepted for resume. The progress endpoint returns the stored value; it does not recalculate a live timer during the HTTP request.

Possible statuses:

- `accepted`
- `processing`
- `created`
- `cooldown`
- `created_and_activated`
- `failed`

## CSV Format

Required columns:

- `name`
- `address`

Optional columns:

- `phone`

Example:

```csv
name,address,phone
City General Hospital,123 Main St Springfield IL 62701,555-100-1001
Riverside Medical Center,456 Oak Ave Portland OR 97201,555-100-1002
```

The current maximum batch size is `20` hospital rows.

## Local Development

Install dependencies:

```bash
uv sync --group dev
```

Run locally:

```bash
HOSPITALS_API_URL=https://hospital-directory.onrender.com uv run uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Run tests:

```bash
uv run pytest
```

Run lint:

```bash
uv run ruff check .
```

## Docker

Build and run with Docker Compose:

```bash
docker compose build
docker compose up
```

The compose file exposes the service at:

```text
http://localhost:8000
```

## Deployment

The service is deployed on Render:

```text
https://hospital-bulk-processor-6466.onrender.com
```

Because the `Dockerfile`, `pyproject.toml`, `uv.lock`, `server.py`, and `internal/` directory are all at the repository root, the Render source/root directory should be left blank when the Render service is connected directly to this repository.

Required Render environment variable:

```text
HOSPITALS_API_URL=https://hospital-directory.onrender.com
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Pipeline Behavior](docs/PIPELINE.md)
- [Functionalities](docs/FUNCTIONALITIES.md)
- [Sample Client Program](docs/SAMPLE_CLIENT.md)
- [Deployment](docs/DEPLOYMENT.md)
