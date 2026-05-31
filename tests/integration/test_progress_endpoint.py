# tests/integration/test_progress_endpoint.py
import pytest
from fastapi.testclient import TestClient


def test_bulk_returns_202_with_batch_id():
    from server import app, limiter
    limiter.reset()
    client = TestClient(app)
    csv_content = b"name,address,phone\nGeneral Hospital,1 Main St,555-0001\n"
    response = client.post(
        "/hospitals/bulk",
        files={"file": ("hospitals.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 202
    data = response.json()
    assert "batch_id" in data


def test_progress_returns_404_for_unknown_batch():
    from server import app
    client = TestClient(app)
    response = client.get("/hospitals/batch/nonexistent-id/progress")
    assert response.status_code == 404


def test_progress_returns_state_for_known_batch():
    from server import app, store
    from internal.models.batch import BatchState, BatchStatus
    store.set("test-batch", BatchState(batch_id="test-batch", status=BatchStatus.PROCESSING, total_hospitals=5))
    client = TestClient(app)
    response = client.get("/hospitals/batch/test-batch/progress")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert data["total_hospitals"] == 5
