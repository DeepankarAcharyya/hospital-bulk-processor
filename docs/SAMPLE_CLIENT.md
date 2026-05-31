# Sample Client Program

The repository includes a simple Python client:

```text
test_client/client.py
```

It uploads a CSV file to the bulk processor API using `httpx`.

## Default Target

The sample client is configured to use the deployed Render service by default:

```text
https://hospital-bulk-processor-6466.onrender.com
```

To target a local server, pass `--url`.

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
uv run python test_client/client.py --json
```

Expected accepted response:

```json
{
  "batch_id": "f8b38c3e-2f08-48c8-8f7d-b7a0800d00c2"
}
```

The API is asynchronous, so this response means the batch was accepted and queued. Use the returned `batch_id` to check progress.

## Run Against Local Service

Start the API locally:

```bash
HOSPITALS_API_URL=https://hospital-directory.onrender.com uv run uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Upload the sample CSV:

```bash
uv run python test_client/client.py --url http://localhost:8000 --json
```

## Use A Custom CSV

```bash
uv run python test_client/client.py --csv path/to/hospitals.csv --json
```

With a local target:

```bash
uv run python test_client/client.py --url http://localhost:8000 --csv path/to/hospitals.csv --json
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
  "status": "completed",
  "total_hospitals": 5,
  "processed_hospitals": 5,
  "failed_hospitals": 0,
  "batch_activated": true,
  "error_message": null
}
```

## Validation Errors

If the CSV is invalid, the API returns `422` and the client prints the validation errors.

Example:

```text
[validation errors]
  row 0: Missing columns: {'name'}
```

## Current Client Note

Because the API now returns `202 Accepted` immediately, use `--json` when running the sample client. Progress details are available from the progress endpoint.
