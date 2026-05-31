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
- Activates the upstream batch only after all rows are created successfully.

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
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2"
}
```

The endpoint returns `202 Accepted` when the CSV is valid and the batch is queued.

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
  "batch_activated": false,
  "error_message": null
}
```

Possible statuses:

- `accepted`
- `processing`
- `cooldown`
- `completed`
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
