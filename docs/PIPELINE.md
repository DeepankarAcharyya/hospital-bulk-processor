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

At this point, the HTTP request is complete, but the batch is still being processed in the background. `POST /hospitals/bulk` returns immediately with `202 Accepted` and initial progress, not the final completed result. The final comprehensive result is available through the progress endpoint after the worker finishes. This matches an async bulk-processing design, but if an assignment expects the upload request itself to block until all hospitals are activated, then this is not an exact match.

## 4. Worker Processes Rows

The background worker consumes the queued batch and marks it as:

```text
processing
```

For each hospital row, it calls the upstream hospital directory API:

```http
POST /hospitals/
```

Rows are counted from `1` in worker logs and error messages. These numbers are intended to identify the first, second, third, etc. parsed hospital record for humans, rather than expose Python's zero-based list indexes.

The payload includes the parsed hospital fields plus:

```json
{
  "creation_batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2"
}
```

After each row, the worker updates:

- `processed_hospitals`
- `failed_hospitals`
- `processing_time_seconds`
- `status`
- `hospitals`

The worker stores one row result per parsed hospital. A row starts as `accepted`, moves to `processing`, and becomes `created` after the upstream create call returns a hospital ID. Failed rows are marked `failed`.

`processing_time_seconds` is measured from the moment the worker begins processing the batch. It is set to `0` when processing starts, then saved back to `InMemoryStore` after row updates, failure paths, activation, and final completion.

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

If a row exhausts all retry attempts for transient failures, that row increments `failed_hospitals`. The worker continues processing later rows, but batch activation is skipped because the batch has failures.

## 7. Batch Activation

If all rows are created successfully, the worker activates the upstream batch:

```http
PATCH /hospitals/batch/{batch_id}/activate
```

If activation succeeds:

- `status` moves from `created` to `created_and_activated`.
- `batch_activated` becomes `true`.
- created row results become `created_and_activated`.

If activation fails:

- `status` becomes `failed`.
- `error_message` explains the activation failure.

## 8. Resume Failed Batch

Clients can resume a known batch with:

```http
POST /hospitals/batch/{batch_id}/resume
```

The endpoint reads the current batch state from memory.

- Unknown batch IDs return `404 Not Found`.
- `created_and_activated` batches return `200 OK` because there is nothing to resume.
- `failed` batches reset aggregate progress, requeue the saved parsed hospital rows, and return `202 Accepted`.
- `accepted`, `processing`, `created`, and `cooldown` batches return `409 Conflict` because they are already active.

Resume rebuilds queue items from the raw `address` and `phone` values stored inside the in-memory batch state. Bulk and progress responses exclude those raw fields. If the process restarts, both progress and resume state are lost.

Resume resets `processing_time_seconds` to `0` before requeueing the saved hospital rows. The next worker run records a fresh elapsed time for the resumed attempt.

## 9. Progress Polling

Clients can poll:

```http
GET /hospitals/batch/{batch_id}/progress
```

Example response:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2",
  "status": "created_and_activated",
  "total_hospitals": 5,
  "processed_hospitals": 5,
  "failed_hospitals": 0,
  "processing_time_seconds": 250,
  "batch_activated": true,
  "hospitals": [
    {
      "row": 1,
      "hospital_id": 101,
      "name": "General Hospital",
      "status": "created_and_activated"
    }
  ],
  "error_message": null
}
```

`processing_time_seconds` is returned from the stored batch state. The progress endpoint does not calculate a live timer; it reports the latest value written by the worker.

Unknown batch IDs return `404 Not Found`.

## Status Reference

| Status | Meaning |
| --- | --- |
| `accepted` | CSV was accepted and queued. |
| `processing` | Worker is processing hospital rows. |
| `created` | All hospital rows were created upstream and activation is about to run. |
| `cooldown` | A circuit breaker is open and the worker is waiting before retrying. |
| `created_and_activated` | All rows were created and the upstream batch was activated. |
| `failed` | The batch failed during row creation or activation. |

## Important Operational Note

Progress is stored in memory. If the service restarts while a batch is running, the stored progress for that batch is lost.
