# Sample Client Program

The repository includes a simple Python client:

```text
test_client/client.py
```

It uploads a CSV file to the bulk processor API using `httpx`, polls the progress endpoint until the batch reaches a terminal state, and can resume a failed batch by ID.

## Default Target

The sample client is configured to use the deployed Render service by default:

```text
https://hospital-bulk-processor-6466.onrender.com
```

To target a local server, pass `--url`.

## Client Options

| Option | Purpose |
| --- | --- |
| `--url` | Override the API base URL. |
| `--csv` | Upload a custom CSV file. |
| `--batch-id` | Poll an existing batch without uploading a new CSV. |
| `--resume` | Resume `--batch-id` before polling. Only failed batches are requeued. |
| `--poll-interval` | Set seconds between progress polls. Default: `2.0`. |
| `--json` | Print the final progress response as JSON. |

## Sample CSV

The default CSV file is:

```text
test_client/hospitals.csv
```

Example format:

```csv
name,address,phone
City General Hospital,123 Main St Springfield IL 62701,555-100-1001
Riverside Medical Center,456 Oak Ave Portland OR 97201,555-100-1002
```

## Run Against Deployed Service

```bash
uv run python test_client/client.py
```

Expected flow:

```text
[health] {'message': 'Hello World'}

[uploading] test_client/hospitals.csv -> https://hospital-bulk-processor-6466.onrender.com/hospitals/bulk
[accepted] batch_id=f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2
[polling]
  [accepted] processed=0/5 failed=0 elapsed=0s
  [processing] processed=3/5 failed=0 elapsed=42.7s
  [created_and_activated] processed=5/5 failed=0 elapsed=250s
```

The upload response uses the same batch state shape as progress polling:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2",
  "status": "accepted",
  "total_hospitals": 5,
  "processed_hospitals": 0,
  "failed_hospitals": 0,
  "processing_time_seconds": 0,
  "batch_activated": false,
  "hospitals": [
    {
      "row": 1,
      "hospital_id": null,
      "name": "City General Hospital",
      "status": "accepted"
    }
  ],
  "error_message": null
}
```

`POST /hospitals/bulk` returns immediately with `202 Accepted` and initial progress, not the final completed result. The final comprehensive result is available through the progress endpoint after the worker finishes. This matches an async bulk-processing design, but if an assignment expects the upload request itself to block until all hospitals are activated, then this is not an exact match.

The sample client handles that automatically by polling:

```http
GET /hospitals/batch/{batch_id}/progress
```

The `elapsed=...s` value printed during polling comes from `processing_time_seconds`. It is the last elapsed worker time stored by the API, not a timer calculated by the client.

## Run Against Local Service

Start the API locally:

```bash
HOSPITALS_API_URL=https://hospital-directory.onrender.com uv run uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Upload the sample CSV:

```bash
uv run python test_client/client.py --url http://localhost:8000
```

## Use A Custom CSV

```bash
uv run python test_client/client.py --csv path/to/hospitals.csv
```

With a local target:

```bash
uv run python test_client/client.py --url http://localhost:8000 --csv path/to/hospitals.csv
```

## Poll An Existing Batch

Use `--batch-id` to skip upload and poll a batch that was already accepted:

```bash
uv run python test_client/client.py --batch-id f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2
```

With a custom polling interval:

```bash
uv run python test_client/client.py --batch-id f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2 --poll-interval 5
```

## Resume A Failed Batch

Use `--resume` with `--batch-id` to call the resume endpoint before polling:

```bash
uv run python test_client/client.py --batch-id f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2 --resume
```

Expected flow when the batch is failed and can be requeued:

```text
[health] {'message': 'Hello World'}

[resuming] batch_id=f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2
[resume] Batch resume accepted

[polling] batch_id=f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2
  [accepted] processed=0/5 failed=0 elapsed=0s
  [processing] processed=2/5 failed=0 elapsed=18.4s
  [created_and_activated] processed=5/5 failed=0 elapsed=250s
```

If the batch is already `completed`, the resume endpoint returns success and the client still polls the final progress state. If the batch is currently `accepted`, `processing`, `created`, or `cooldown`, the client prints an error because active batches cannot be resumed. When a failed batch is accepted for resume, the API resets `processing_time_seconds` to `0` before the worker starts again.

## Print Raw Final JSON

Use `--json` to print the final progress response after polling completes:

```bash
uv run python test_client/client.py --json
```

## Poll Progress

After upload, copy the returned `batch_id` and call:

```bash
curl https://hospital-bulk-processor-6466.onrender.com/hospitals/batch/{batch_id}/progress
```

Local example:

```bash
curl http://localhost:8000/hospitals/batch/{batch_id}/progress
```

Example completed response:

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

## Resume With Curl

```bash
curl -X POST https://hospital-bulk-processor-6466.onrender.com/hospitals/batch/{batch_id}/resume
```

Local example:

```bash
curl -X POST http://localhost:8000/hospitals/batch/{batch_id}/resume
```

Successful resume of a failed batch returns:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2",
  "status": "accepted",
  "message": "Batch resume accepted"
}
```

## Validation Errors

If the CSV is invalid, the API returns `422` and the client prints the validation errors.

Example:

```text
[validation errors]
  row 0: Missing columns: {'name'}
```

## Polling Behavior

The client polls every `2` seconds by default. It stops when the batch status is either:

- `created_and_activated`
- `failed`

If the service reports `cooldown`, the client keeps polling and annotates the line as circuit breaker cooldown.
