import httpx
import pytest

from test_client.client import resume_batch


def _client_with_response(status_code: int, payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/hospitals/batch/batch-1/resume"
        return httpx.Response(status_code, json=payload)

    return httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(handler))


def test_resume_batch_returns_accepted_response(capsys):
    with _client_with_response(
        202,
        {
            "batch_id": "batch-1",
            "status": "accepted",
            "message": "Batch resume accepted",
        },
    ) as client:
        data = resume_batch(client, "batch-1")

    assert data["status"] == "accepted"
    assert "[resume] Batch resume accepted" in capsys.readouterr().out


def test_resume_batch_returns_completed_response(capsys):
    with _client_with_response(
        200,
        {
            "batch_id": "batch-1",
            "status": "completed",
            "message": "Batch already completed",
        },
    ) as client:
        data = resume_batch(client, "batch-1")

    assert data["status"] == "completed"
    assert "[resume] Batch already completed" in capsys.readouterr().out


def test_resume_batch_exits_when_batch_not_found():
    with _client_with_response(404, {"detail": "Batch not found"}) as client:
        with pytest.raises(SystemExit) as exc_info:
            resume_batch(client, "batch-1")

    assert exc_info.value.code == 1


def test_resume_batch_exits_when_batch_is_active():
    with _client_with_response(409, {"detail": "Only failed batches can be resumed"}) as client:
        with pytest.raises(SystemExit) as exc_info:
            resume_batch(client, "batch-1")

    assert exc_info.value.code == 1
