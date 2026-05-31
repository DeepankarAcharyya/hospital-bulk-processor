import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("HOSPITALS_API_URL", "http://test-hospitals-api")

from server import app  # noqa: E402 — env var must be set before import


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_csv(rows: list[str]) -> bytes:
    return "\n".join(rows).encode()


def valid_csv(n: int = 2) -> bytes:
    lines = ["name,address,phone"]
    for i in range(1, n + 1):
        lines.append(f"Hospital {i},123 Main St {i},555-000{i}")
    return "\n".join(lines).encode()
