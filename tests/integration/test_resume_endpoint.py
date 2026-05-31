from uuid import uuid4

from internal.models.batch import BatchState, BatchStatus
from internal.models.hospital import Hospital


class FakeQueue:
    def __init__(self) -> None:
        self.items = []

    async def put(self, item):
        self.items.append(item)


def _hospital(name: str = "Resume Hospital") -> Hospital:
    return Hospital(name=name, address="1 Main St")


def test_resume_returns_404_for_unknown_batch(client):
    response = client.post("/hospitals/batch/missing-batch/resume")
    assert response.status_code == 404


def test_resume_completed_batch_returns_success(client):
    from server import store

    batch_id = str(uuid4())
    store.set(
        batch_id,
        BatchState(
            batch_id=batch_id,
            status=BatchStatus.COMPLETED,
            total_hospitals=1,
            processed_hospitals=1,
            batch_activated=True,
        ),
    )

    response = client.post(f"/hospitals/batch/{batch_id}/resume")

    assert response.status_code == 200
    assert response.json() == {
        "batch_id": batch_id,
        "status": "completed",
        "message": "Batch already completed",
    }


def test_resume_failed_batch_requeues_saved_payload_and_returns_202(client, monkeypatch):
    import server

    batch_id = str(uuid4())
    hospitals = [_hospital("Hospital A"), _hospital("Hospital B")]
    fake_queue = FakeQueue()
    monkeypatch.setattr(server, "job_queue", fake_queue)

    server.store.set(
        batch_id,
        BatchState(
            batch_id=batch_id,
            status=BatchStatus.FAILED,
            total_hospitals=2,
            processed_hospitals=1,
            failed_hospitals=1,
            error_message="Row 2: fatal 422",
        ),
    )
    server.batch_payloads[batch_id] = hospitals

    response = client.post(f"/hospitals/batch/{batch_id}/resume")

    assert response.status_code == 202
    assert response.json() == {
        "batch_id": batch_id,
        "status": "accepted",
        "message": "Batch resume accepted",
    }
    assert fake_queue.items == [(batch_id, hospitals)]

    state = server.store.get(batch_id)
    assert state.status == BatchStatus.ACCEPTED
    assert state.processed_hospitals == 0
    assert state.failed_hospitals == 0
    assert state.batch_activated is False
    assert state.error_message is None


def test_resume_active_batch_returns_409(client):
    from server import store

    batch_id = str(uuid4())
    store.set(
        batch_id,
        BatchState(batch_id=batch_id, status=BatchStatus.PROCESSING, total_hospitals=2),
    )

    response = client.post(f"/hospitals/batch/{batch_id}/resume")

    assert response.status_code == 409
