# Pipeline Behavior

This document describes how a hospital CSV moves through the service from upload to final activation.

## 1. Client Uploads CSV

The client uploads a CSV file to:

```http
POST /hospitals/bulk
```

The request must be `multipart/form-data`, and the file field must be named `file`.

Accepted content types:

- `text/csv`
- `application/csv`

Other content types return `400 Bad Request`.

## 2. CSV Is Parsed And Validated

The service reads the uploaded file and validates it with `parse_and_validate_csv`.

Validation rules:

- The file must contain a header row.
- Required columns: `name`, `address`.
- Optional column: `phone`.
- Header names are stripped and lowercased.
- Cell values are stripped.
- Empty optional fields are converted to `null`.
- Unknown columns are ignored.
- Maximum batch size is `20` rows.

If validation fails, the service returns `422 Unprocessable Entity` with an `errors` array.

Example:

```json
{
  "errors": [
    {
      "row": 0,
      "error": "Missing columns: {'name'}"
    }
  ]
}
```

## 3. Batch Is Accepted

For a valid CSV, the service:

1. Generates a UUID batch ID.
2. Stores initial state in `InMemoryStore`.
3. Enqueues the parsed hospital list on an `asyncio.Queue`.
4. Returns `202 Accepted`.

Example:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2"
}
```

At this point, the HTTP request is complete, but the batch is still being processed in the background.

## 4. Worker Processes Rows

The background worker consumes the queued batch and marks it as:

```text
processing
```

For each hospital row, it calls the upstream hospital directory API:

```http
POST /hospitals/
```

The payload includes the parsed hospital fields plus:

```json
{
  "creation_batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2"
}
```

After each row, the worker updates:

- `processed_hospitals`
- `failed_hospitals`
- `status`

## 5. Retry And Cooldown Behavior

The worker retries transient create failures up to six attempts per row.

Transient failures include:

- HTTP transport errors.
- Upstream `429` responses.
- Upstream `503` responses.

Retries use exponential backoff capped at `60` seconds.

If the create circuit breaker is open, the batch status is set to:

```text
cooldown
```

The worker sleeps for the circuit breaker's `retry_after` duration, then tries again.

## 6. Fatal Row Failures

If the upstream create request returns `400` or `422`, the worker treats it as a fatal data error.

In that case:

1. The batch is marked `failed`.
2. `error_message` is set with the row and status code.
3. Processing stops.
4. Later rows are not attempted.
5. Batch activation is skipped.

## 7. Batch Activation

If all rows are created successfully, the worker activates the upstream batch:

```http
PATCH /hospitals/batch/{batch_id}/activate
```

If activation succeeds:

- `status` becomes `completed`.
- `batch_activated` becomes `true`.

If activation fails:

- `status` becomes `failed`.
- `error_message` explains the activation failure.

## 8. Progress Polling

Clients can poll:

```http
GET /hospitals/batch/{batch_id}/progress
```

Example response:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2",
  "status": "completed",
  "total_hospitals": 5,
  "processed_hospitals": 5,
  "failed_hospitals": 0,
  "batch_activated": true,
  "error_message": null
}
```

Unknown batch IDs return `404 Not Found`.

## Status Reference

| Status | Meaning |
| --- | --- |
| `accepted` | CSV was accepted and queued. |
| `processing` | Worker is processing hospital rows. |
| `cooldown` | A circuit breaker is open and the worker is waiting before retrying. |
| `completed` | All rows were created and the upstream batch was activated. |
| `failed` | The batch failed during row creation or activation. |

## Important Operational Note

Progress is stored in memory. If the service restarts while a batch is running, the stored progress for that batch is lost.
