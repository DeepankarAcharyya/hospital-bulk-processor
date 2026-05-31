from uuid import uuid4

from internal.models.batch import BatchState, BatchStatus


def test_bulk_returns_202_with_batch_id(client):
    csv_content = b"name,address,phone\nGeneral Hospital,1 Main St,555-0001\n"
    response = client.post(
        "/hospitals/bulk",
        files={"file": ("hospitals.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 202
    data = response.json()
    assert "batch_id" in data


def test_progress_returns_404_for_unknown_batch(client):
    response = client.get("/hospitals/batch/nonexistent-id/progress")
    assert response.status_code == 404


def test_progress_returns_state_for_known_batch(client):
    from server import store
    unique_id = str(uuid4())
    store.set(unique_id, BatchState(batch_id=unique_id, status=BatchStatus.PROCESSING, total_hospitals=5))
    response = client.get(f"/hospitals/batch/{unique_id}/progress")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert data["total_hospitals"] == 5
