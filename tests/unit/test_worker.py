import asyncio
import pytest
import httpx
import respx
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from internal.models.batch import BatchState, BatchStatus
from internal.store.in_memory import InMemoryStore
from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
from internal.models.hospital import Hospital


def _make_hospital(name: str = "Test Hospital") -> Hospital:
    return Hospital(name=name, address="1 Main St")


async def _run_worker_once(store, hospitals, batch_id=None):
    """Helper: run process_batch_job directly (bypasses queue)."""
    from server import process_batch_job
    bid = str(batch_id or uuid4())
    store.set(bid, BatchState(batch_id=bid, status=BatchStatus.ACCEPTED, total_hospitals=len(hospitals)))
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


@respx.mock
async def test_row_failure_exhausts_retries_marks_failed(respx_mock):
    respx_mock.post("/hospitals/").mock(side_effect=httpx.ConnectError("down"))
    store = InMemoryStore()
    with patch("asyncio.sleep", new_callable=AsyncMock):  # skip backoff sleeps
        bid = await _run_worker_once(store, [_make_hospital()])
    state = store.get(bid)
    assert state.failed_hospitals == 1
    assert state.batch_activated is False


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

    with patch("asyncio.sleep", new_callable=AsyncMock):
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
    # Only 1 POST made — aborted immediately on first 422, row 2 never attempted
    assert respx_mock.calls.call_count == 1
