import httpx
import pytest

from test_client.client import poll_progress, print_results, resume_batch


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
            "status": "created_and_activated",
            "message": "Batch already completed",
        },
    ) as client:
        data = resume_batch(client, "batch-1")

    assert data["status"] == "created_and_activated"
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


def test_print_results_includes_processing_time_and_hospitals(capsys):
    print_results(
        {
            "batch_id": "batch-1",
            "status": "created_and_activated",
            "total_hospitals": 1,
            "processed_hospitals": 1,
            "failed_hospitals": 0,
            "processing_time_seconds": 2.5,
            "batch_activated": True,
            "hospitals": [
                {
                    "row": 1,
                    "hospital_id": 101,
                    "name": "General Hospital",
                    "status": "created_and_activated",
                }
            ],
            "error_message": None,
        }
    )

    output = capsys.readouterr().out
    assert "processing_time_seconds : 2.5" in output
    assert "row=1 id=101 name=General Hospital status=created_and_activated" in output


def test_poll_progress_prints_last_recorded_worker_elapsed_time(capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/hospitals/batch/batch-1/progress"
        return httpx.Response(
            200,
            json={
                "batch_id": "batch-1",
                "status": "created_and_activated",
                "total_hospitals": 1,
                "processed_hospitals": 1,
                "failed_hospitals": 0,
                "processing_time_seconds": 2.5,
                "batch_activated": True,
                "hospitals": [],
                "error_message": None,
            },
        )

    with httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(handler)) as client:
        data = poll_progress(client, "batch-1", poll_interval=0)

    output = capsys.readouterr().out
    assert data["processing_time_seconds"] == 2.5
    assert "elapsed=2.5s" in output
