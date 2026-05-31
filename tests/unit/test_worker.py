import asyncio
import httpx
import respx
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from internal.models.batch import BatchState, BatchStatus
from internal.models.bulk_response import HospitalResult
from internal.store.in_memory import InMemoryStore
from internal.clients.circuit_breaker import CircuitBreaker
from internal.models.hospital import Hospital


def _make_hospital(name: str = "Test Hospital") -> Hospital:
    return Hospital(name=name, address="1 Main St")


def _hospital_results(hospitals: list[Hospital]) -> list[HospitalResult]:
    return [
        HospitalResult(
            row=row,
            hospital_id=None,
            name=hospital.name,
            address=hospital.address,
            phone=hospital.phone,
            status="accepted",
        )
        for row, hospital in enumerate(hospitals, start=1)
    ]


async def _run_worker_once(store, hospitals, batch_id=None):
    """Helper: run process_batch_job directly (bypasses queue)."""
    from server import process_batch_job
    bid = str(batch_id or uuid4())
    store.set(
        bid,
        BatchState(
            batch_id=bid,
            status=BatchStatus.ACCEPTED,
            total_hospitals=len(hospitals),
            hospitals=_hospital_results(hospitals),
        ),
    )
    create_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
    activate_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
    api_lock = asyncio.Lock()
    await process_batch_job(bid, hospitals, store, create_breaker, activate_breaker, api_lock)
    return bid


@respx.mock
async def test_happy_path_marks_completed(respx_mock):
    respx_mock.post("/hospitals/").mock(return_value=httpx.Response(201, json={"id": 1}))
    respx_mock.patch(url__regex=r"/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    store = InMemoryStore()
    hospitals = [_make_hospital(f"H{i}") for i in range(3)]
    bid = await _run_worker_once(store, hospitals)
    state = store.get(bid)
    assert state.status == BatchStatus.COMPLETED
    assert state.processed_hospitals == 3
    assert state.batch_activated is True
    assert state.processing_time_seconds > 0
    assert [hospital.status for hospital in state.hospitals] == [
        "created_and_activated",
        "created_and_activated",
        "created_and_activated",
    ]
    assert [hospital.hospital_id for hospital in state.hospitals] == [1, 1, 1]
    assert state.hospitals[0].address == "1 Main St"


@respx.mock
async def test_row_failure_exhausts_retries_marks_failed(respx_mock):
    respx_mock.post("/hospitals/").mock(side_effect=httpx.ConnectError("down"))
    store = InMemoryStore()
    with patch("asyncio.sleep", new_callable=AsyncMock):  # skip backoff sleeps
        bid = await _run_worker_once(store, [_make_hospital()])
    state = store.get(bid)
    assert state.failed_hospitals == 1
    assert state.batch_activated is False
    assert state.status == BatchStatus.FAILED
    assert state.hospitals[0].status == "failed"


@respx.mock
async def test_retries_six_times_then_continues(respx_mock):
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 6:
            raise httpx.ConnectError("down")
        return httpx.Response(201, json={"id": 1})

    respx_mock.post("/hospitals/").mock(side_effect=side_effect)
    respx_mock.patch(url__regex=r"/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    store = InMemoryStore()
    # Two hospitals: first exhausts 6 retries (fails), second succeeds on attempt 7 overall
    # but reset per-row, so second row has fresh attempt counter
    hospitals = [_make_hospital("fail"), _make_hospital("ok")]

    # Mock both asyncio.sleep (to skip waits) and time.monotonic (to advance time for circuit breaker)
    monotonic_time = [0.0]  # use list to allow mutation in nested function

    def mock_monotonic():
        return monotonic_time[0]

    async def mock_sleep(duration):
        monotonic_time[0] += duration

    with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=mock_sleep), \
         patch("server.time.monotonic", side_effect=mock_monotonic), \
         patch("internal.clients.circuit_breaker.time.monotonic", side_effect=mock_monotonic):
        bid = await _run_worker_once(store, hospitals)
    state = store.get(bid)
    assert state.failed_hospitals == 1
    assert state.processed_hospitals == 1


@respx.mock
async def test_fatal_data_error_aborts_batch_immediately(respx_mock):
    respx_mock.post("/hospitals/").mock(
        return_value=httpx.Response(422, json={"detail": "Unprocessable Entity"})
    )
    store = InMemoryStore()
    # Two hospitals — only first should be attempted; 422 aborts entire batch
    hospitals = [_make_hospital("bad-data"), _make_hospital("also-bad")]
    bid = await _run_worker_once(store, hospitals)
    state = store.get(bid)
    assert state.status == BatchStatus.FAILED
    assert state.hospitals[0].status == "failed"
    assert state.hospitals[1].status == "accepted"
    # Only 1 POST made — aborted immediately on first 422, row 2 never attempted
    assert respx_mock.calls.call_count == 1


@respx.mock
async def test_successful_batch_moves_through_created_before_completed(respx_mock):
    class RecordingStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.statuses = []

        def update(self, batch_id: str, **kwargs) -> None:
            super().update(batch_id, **kwargs)
            state = self.get(batch_id)
            self.statuses.append(state.status)

    respx_mock.post("/hospitals/").mock(return_value=httpx.Response(201, json={"id": 101}))
    respx_mock.patch(url__regex=r"/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )

    store = RecordingStore()
    bid = await _run_worker_once(store, [_make_hospital("General Hospital")])

    assert store.get(bid).status == BatchStatus.COMPLETED
    assert BatchStatus.CREATED in store.statuses
    assert store.statuses.index(BatchStatus.CREATED) < store.statuses.index(BatchStatus.COMPLETED)
