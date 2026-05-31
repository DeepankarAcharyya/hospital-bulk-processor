# Deployment

The Hospital Bulk Processor is deployed on Render.

Public URL:

```text
https://hospital-bulk-processor-6466.onrender.com
```

## Render Configuration

Use Docker-based deployment.

Recommended settings:

| Setting | Value |
| --- | --- |
| Environment | Docker |
| Root Directory | Leave blank when Render is connected to this repo directly |
| Dockerfile Path | `Dockerfile` |
| Health Check Path | `/` |
| Port | `8000` |

## Deployment Pipeline

Deployment is gated by the GitHub Actions test job.

Pull requests into `main` or `deployed` run the test workflow. Pushes to the `deployed` branch run the deployment workflow, which first installs dependencies and runs:

```bash
uv run pytest
```

The Render deployment is triggered only after those tests pass. The deploy job depends on the test job and calls the Render deploy hook through `RENDER_DEPLOY_HOOK_URL` only on a successful test result.

## Source Folder

The `Dockerfile` expects these files and folders at the build context root:

```text
Dockerfile
pyproject.toml
uv.lock
server.py
internal/
```

In this repository, all of those are at the Git repository root. Therefore:

```text
Root Directory: leave blank
```

Only set the root directory to `hospital-bulk-processor` if Render is connected to a parent repository that contains this project as a subfolder.

## Environment Variables

Required:

```text
HOSPITALS_API_URL=https://hospital-directory.onrender.com
```

This value tells the bulk processor where to send hospital creation and batch activation requests.

## Container Behavior

The Docker image:

1. Starts from `python:3.11-slim`.
2. Installs `uv`.
3. Installs production dependencies from `pyproject.toml` and `uv.lock`.
4. Copies `internal/` and `server.py`.
5. Runs Uvicorn.

Runtime command:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

## Docker Compose

For local Docker testing:

```bash
docker compose build
docker compose up
```

The service will be available at:

```text
http://localhost:8000
```

The local compose file sets:

```text
HOSPITALS_API_URL=https://hospital-directory.onrender.com
```

## Deployment Smoke Tests

Health check:

```bash
curl https://hospital-bulk-processor-6466.onrender.com/
```

Expected response:

```json
{
  "message": "Hello World"
}
```

Upload sample CSV:

```bash
uv run python test_client/client.py
```

The client should print an accepted `batch_id`, poll progress, and end with either `created_and_activated` or `failed`.

Resume a failed batch by ID:

```bash
uv run python test_client/client.py --batch-id {batch_id} --resume
```
