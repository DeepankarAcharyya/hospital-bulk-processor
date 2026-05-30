.PHONY: install install-dev sync dev run test lint format clean

install:
	uv sync --no-dev

install-dev:
	uv sync --group dev

sync:
	uv sync --group dev

dev:
	uv run uvicorn server:app --reload --host 0.0.0.0 --port 8000 --reload

run:
	uv run uvicorn server:app --host 0.0.0.0 --port 8000

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

clean:
	rm -rf .venv __pycache__ .pytest_cache dist
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete