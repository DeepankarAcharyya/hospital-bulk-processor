import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from internal.batch_utils import (
    batch_response,
    hospitals_from_state,
    initial_hospital_results,
    mark_all_hospital_results,
    update_hospital_result,
)
from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
from internal.clients.hospital_client import create_hospital, activate_batch
from internal.logging_config import configure_logging
from internal.models.batch import BatchState, BatchStatus
from internal.store.in_memory import InMemoryStore
from internal.validation.csv import parse_and_validate_csv, CSVValidationError

configure_logging()

log = structlog.get_logger(__name__)

HOSPITALS_API_URL = os.environ["HOSPITALS_API_URL"]

limiter = Limiter(key_func=get_remote_address)

job_queue      = asyncio.Queue()
api_lock       = asyncio.Lock()
create_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
activate_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
store          = InMemoryStore()


async def process_batch_job(
    batch_id: str,
    hospitals: list,
    store: InMemoryStore,
    create_breaker: CircuitBreaker,
    activate_breaker: CircuitBreaker,
    api_lock: asyncio.Lock,
) -> None:
    started_at = time.perf_counter()
    store.update(batch_id, status=BatchStatus.PROCESSING, processing_time_seconds=0.0)
    failed = 0

    async with httpx.AsyncClient(base_url=HOSPITALS_API_URL, timeout=30.0) as client:
        for row, hospital in enumerate(hospitals, start=1):
            attempt = 0
            success = False
            state = store.get(batch_id)
            if state is not None:
                store.update(
                    batch_id,
                    hospitals=update_hospital_result(state, row, status=BatchStatus.PROCESSING.value),
                )

            while attempt < 6:
                try:
                    async with create_breaker:
                        async with api_lock:
                            result = await create_hospital(client, hospital, uuid.UUID(batch_id), row)
                    state = store.get(batch_id)
                    if state is not None:
                        store.update(
                            batch_id,
                            hospitals=update_hospital_result(
                                state,
                                row,
                                status=BatchStatus.CREATED.value,
                                hospital_id=result.hospital_id,
                            ),
                        )
                    success = True
                    break
                except CircuitOpenError as e:
                    store.update(batch_id, status=BatchStatus.COOLDOWN)
                    log.warning("create_circuit_open", batch_id=batch_id, row=row, attempt=attempt, retry_after=e.retry_after)
                    await asyncio.sleep(e.retry_after)
                    attempt += 1
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (400, 422):
                        log.error("create_fatal_data_error", batch_id=batch_id, row=row, status_code=e.response.status_code)
                        state = store.get(batch_id)
                        store.update(
                            batch_id,
                            status=BatchStatus.FAILED,
                            failed_hospitals=failed + 1,
                            processing_time_seconds=time.perf_counter() - started_at,
                            hospitals=update_hospital_result(state, row, status=BatchStatus.FAILED.value) if state else [],
                            error_message=f"Row {row}: fatal {e.response.status_code}",
                        )
                        return
                    backoff = min(60.0, 2.0 ** attempt)
                    log.warning("create_transient_retry", batch_id=batch_id, row=row, attempt=attempt, backoff=backoff, status_code=e.response.status_code)
                    await asyncio.sleep(backoff)
                    attempt += 1
                except httpx.TransportError as e:
                    backoff = min(60.0, 2.0 ** attempt)
                    log.warning("create_transport_retry", batch_id=batch_id, row=row, attempt=attempt, backoff=backoff, error=repr(e))
                    await asyncio.sleep(backoff)
                    attempt += 1

            if not success:
                failed += 1
                log.error("create_exhausted_retries", batch_id=batch_id, row=row, name=hospital.name)
                state = store.get(batch_id)
                if state is not None:
                    store.update(
                        batch_id,
                        hospitals=update_hospital_result(state, row, status=BatchStatus.FAILED.value),
                    )

            store.update(
                batch_id,
                status=BatchStatus.PROCESSING,
                processed_hospitals=row - failed,
                failed_hospitals=failed,
                processing_time_seconds=time.perf_counter() - started_at,
            )

        batch_activated = False
        if failed == 0:
            store.update(
                batch_id,
                status=BatchStatus.CREATED,
                processing_time_seconds=time.perf_counter() - started_at,
            )
            try:
                async with activate_breaker:
                    async with api_lock:
                        batch_activated = await activate_batch(client, uuid.UUID(batch_id))
            except CircuitOpenError as e:
                log.error("activate_circuit_open", batch_id=batch_id, retry_after=e.retry_after)
                store.update(
                    batch_id,
                    status=BatchStatus.FAILED,
                    processing_time_seconds=time.perf_counter() - started_at,
                    error_message="Activation circuit open",
                )
                return
            except httpx.HTTPStatusError:
                log.error("batch_activation_failed", batch_id=batch_id)
                store.update(
                    batch_id,
                    status=BatchStatus.FAILED,
                    processing_time_seconds=time.perf_counter() - started_at,
                    error_message="Activation HTTP error",
                )
                return
            except httpx.TransportError:
                log.error("activate_transport_error", batch_id=batch_id)
                store.update(
                    batch_id,
                    status=BatchStatus.FAILED,
                    processing_time_seconds=time.perf_counter() - started_at,
                    error_message="Activation transport error",
                )
                return

    final_status = BatchStatus.COMPLETED if failed == 0 else BatchStatus.FAILED
    state = store.get(batch_id)
    store.update(
        batch_id,
        status=final_status,
        processing_time_seconds=time.perf_counter() - started_at,
        batch_activated=batch_activated,
        hospitals=mark_all_hospital_results(state, "created_and_activated") if state and batch_activated else state.hospitals if state else [],
    )
    log.info("batch_complete", batch_id=batch_id, failed=failed, activated=batch_activated)


async def _worker_loop() -> None:
    while True:
        batch_id, hospitals = await job_queue.get()
        try:
            await process_batch_job(batch_id, hospitals, store, create_breaker, activate_breaker, api_lock)
        except Exception as exc:
            log.error("worker_unhandled_error", batch_id=batch_id, error=repr(exc))
            try:
                store.update(batch_id, status=BatchStatus.FAILED, error_message=repr(exc))
            except KeyError:
                pass
        finally:
            job_queue.task_done()


@asynccontextmanager
async def lifespan(app_: FastAPI):
    task = asyncio.create_task(_worker_loop())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
        client=request.client.host if request.client else None,
    )
    return response


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/hospitals/bulk", status_code=202)
@limiter.limit("10/minute")
async def bulk_create_hospitals(request: Request, file: UploadFile = File(...)):
    if file.content_type not in ("text/csv", "application/csv"):
        log.warning("invalid_content_type", content_type=file.content_type)
        raise HTTPException(status_code=400, detail="File must be a CSV")

    content = await file.read()

    try:
        hospitals = parse_and_validate_csv(content)
    except CSVValidationError as e:
        log.warning("csv_validation_failed", error_count=len(e.errors), errors=e.errors)
        return JSONResponse(status_code=422, content={"errors": e.errors})

    batch_id = str(uuid.uuid4())
    store.set(batch_id, BatchState(
        batch_id=batch_id,
        status=BatchStatus.ACCEPTED,
        total_hospitals=len(hospitals),
        hospitals=initial_hospital_results(hospitals),
    ))
    await job_queue.put((batch_id, hospitals))
    log.info("batch_enqueued", batch_id=batch_id, total=len(hospitals))

    return batch_response(store.get(batch_id))


@app.post("/hospitals/batch/{batch_id}/resume")
@limiter.limit("20/minute")
async def resume_batch(request: Request, batch_id: str):
    state = store.get(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    if state.status == BatchStatus.COMPLETED:
        return {
            "batch_id": batch_id,
            "status": state.status,
            "message": "Batch already completed",
        }

    if state.status != BatchStatus.FAILED:
        raise HTTPException(status_code=409, detail="Only failed batches can be resumed")

    hospitals = hospitals_from_state(state)
    if not hospitals:
        raise HTTPException(status_code=404, detail="Batch payload not found")

    store.update(
        batch_id,
        status=BatchStatus.ACCEPTED,
        total_hospitals=len(hospitals),
        processed_hospitals=0,
        failed_hospitals=0,
        processing_time_seconds=0.0,
        batch_activated=False,
        hospitals=initial_hospital_results(hospitals),
        error_message=None,
    )
    await job_queue.put((batch_id, hospitals))
    log.info("batch_resume_enqueued", batch_id=batch_id, total=len(hospitals))

    return JSONResponse(
        status_code=202,
        content={
            "batch_id": batch_id,
            "status": BatchStatus.ACCEPTED,
            "message": "Batch resume accepted",
        },
    )


@app.get("/hospitals/batch/{batch_id}/progress")
@limiter.limit("60/minute")
async def get_batch_progress(request: Request, batch_id: str):
    state = store.get(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch_response(state)
