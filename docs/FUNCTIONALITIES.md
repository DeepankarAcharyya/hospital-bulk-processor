# Functionalities

## CSV Upload

The service accepts hospital CSV files through:

```http
POST /hospitals/bulk
```

The upload endpoint supports small controlled batches. The current maximum is `20` hospital rows per CSV.

## CSV Validation

Validation happens before a batch is accepted.

Supported columns:

| Column | Required | Notes |
| --- | --- | --- |
| `name` | Yes | Hospital name. |
| `address` | Yes | Hospital street/city/state address text. |
| `phone` | No | Optional contact number. |

Validation behavior:

- Rejects empty files.
- Rejects missing required columns.
- Rejects files over the maximum row count.
- Collects row-level Pydantic validation errors.
- Strips whitespace from headers and values.
- Allows header casing differences by lowercasing headers.
- Ignores unknown columns.

## Async Batch Processing

Valid uploads return immediately with a `batch_id` and status code `202`.

The actual hospital creation work runs in a background worker. This keeps the upload request short and gives clients a stable ID for progress polling.

## Progress Tracking

Clients can poll:

```http
GET /hospitals/batch/{batch_id}/progress
```

The progress response includes:

- `batch_id`
- `status`
- `total_hospitals`
- `processed_hospitals`
- `failed_hospitals`
- `batch_activated`
- `error_message`

The current progress API reports aggregate batch state. It does not return one result object per hospital row.

## Upstream Hospital Creation

Each CSV row is sent to the configured upstream hospital directory API:

```http
POST /hospitals/
```

The service adds the same `creation_batch_id` to every row in a batch so the upstream service can group the records.

The worker reads the upstream create response for logging and status validation, but progress state is updated with counters rather than stored per-row payloads.

## Batch Activation

After every hospital is created successfully, the service calls:

```http
PATCH /hospitals/batch/{batch_id}/activate
```

Activation is skipped when any hospital creation fails.

## Retry And Backoff

The worker retries transient row creation failures.

Retryable conditions include:

- Network or transport failures.
- Upstream `429 Too Many Requests`.
- Upstream `503 Service Unavailable`.

Backoff grows exponentially and is capped at `60` seconds.

## Circuit Breakers

Circuit breakers protect the worker from repeatedly calling an unhealthy upstream service.

There are separate circuit breakers for:

- Hospital creation.
- Batch activation.

When a circuit is open, the batch can temporarily move into `cooldown`.

## Rate Limiting

The bulk upload endpoint is limited to:

```text
10 requests per minute
```

The limit is applied per remote address.

## Structured Logging

The app emits JSON logs through `structlog`.

Logged events include:

- Request method, path, status code, and duration.
- CSV validation failures.
- Batch enqueue events.
- Row creation failures and retries.
- Circuit breaker cooldowns.
- Batch completion or failure.

## Docker Deployment

The repository includes:

- `Dockerfile`
- `docker-compose.yml`

The container runs FastAPI through Uvicorn on port `8000`.

## Sample Client

The `test_client/client.py` script demonstrates how to upload `test_client/hospitals.csv` to either the deployed service or a local service.

The client uploads the CSV, receives a `batch_id`, polls the progress endpoint until the batch reaches `completed` or `failed`, and then prints the final batch state. It can also poll an existing batch through `--batch-id`.

See [Sample Client Program](SAMPLE_CLIENT.md).
