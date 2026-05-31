FROM python:3.11-slim AS base

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock* ./

# Create venv and install prod deps
RUN uv venv /app/.venv && \
    uv sync --no-dev --no-editable --python /app/.venv/bin/python

ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY internal/ ./internal/
COPY server.py ./

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
