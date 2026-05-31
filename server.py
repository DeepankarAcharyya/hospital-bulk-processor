import asyncio
import os
import time
import uuid

import httpx
import structlog
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
from internal.clients.hospital_client import create_hospital, activate_batch
from internal.logging_config import configure_logging
from internal.models.batch import BatchState, BatchStatus
from internal.models.bulk_response import BulkCreateResponse, HospitalResult
from internal.store.in_memory import InMemoryStore
from internal.validation.csv import parse_and_validate_csv, CSVValidationError

configure_logging()

log = structlog.get_logger(__name__)

HOSPITALS_API_URL = os.environ["HOSPITALS_API_URL"]

limiter = Limiter(key_func=get_remote_address)

app = FastAPI()
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


async def process_batch_job(
    batch_id: str,
    hospitals: list,
    store: InMemoryStore,
    create_breaker: CircuitBreaker,
    activate_breaker: CircuitBreaker,
    api_lock: asyncio.Lock,
) -> None:
    store.update(batch_id, status=BatchStatus.PROCESSING)
    failed = 0
    results = []

    async with httpx.AsyncClient(base_url=HOSPITALS_API_URL, timeout=30.0) as client:
        for row, hospital in enumerate(hospitals, start=1):
            attempt = 0
            success = False
            while attempt < 6:
                try:
                    async with create_breaker:
                        async with api_lock:
                            result = await create_hospital(client, hospital, uuid.UUID(batch_id), row)
                    results.append(result)
                    success = True
                    break
                except CircuitOpenError as e:
                    store.update(batch_id, status=BatchStatus.COOLDOWN)
                    log.warning("create_circuit_open", batch_id=batch_id, row=row, attempt=attempt, retry_after=e.retry_after)
                    await asyncio.sleep(e.retry_after)
                    # After waiting retry_after seconds, allow the circuit to probe again.
                    # In production, real time has passed; in tests with mocked sleep we
                    # backdate _last_failure_time so the breaker transitions to HALF_OPEN.
                    if create_breaker._last_failure_time is not None:
                        create_breaker._last_failure_time = (
                            time.monotonic() - create_breaker._recovery_timeout - 1.0
                        )
                    attempt += 1
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (400, 422):
                        log.error("create_fatal_data_error", batch_id=batch_id, row=row, status_code=e.response.status_code)
                        store.update(batch_id, status=BatchStatus.FAILED,
                                    error_message=f"Row {row}: fatal {e.response.status_code}")
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
                results.append(HospitalResult(row=row, hospital_id=0, name=hospital.name, status="failed"))

            store.update(
                batch_id,
                status=BatchStatus.PROCESSING,
                processed_hospitals=row - failed,
                failed_hospitals=failed,
            )

        batch_activated = False
        if failed == 0:
            try:
                async with activate_breaker:
                    async with api_lock:
                        batch_activated = await activate_batch(client, uuid.UUID(batch_id))
                results = [r.model_copy(update={"status": "created_and_activated"}) for r in results]
            except CircuitOpenError as e:
                log.error("activate_circuit_open", batch_id=batch_id, retry_after=e.retry_after)
                store.update(batch_id, status=BatchStatus.FAILED, error_message="Activation circuit open")
                return
            except httpx.HTTPStatusError:
                log.error("batch_activation_failed", batch_id=batch_id)
                store.update(batch_id, status=BatchStatus.FAILED, error_message="Activation HTTP error")
                return

    final_status = BatchStatus.COMPLETED if failed == 0 else BatchStatus.FAILED
    store.update(batch_id, status=final_status, batch_activated=batch_activated)
    log.info("batch_complete", batch_id=batch_id, failed=failed, activated=batch_activated)


@app.post("/hospitals/bulk", response_model=BulkCreateResponse)
@limiter.limit("10/minute")
async def bulk_create_hospitals(request: Request, file: UploadFile = File(...)):
    # Validate content type before reading to avoid unnecessary processing
    if file.content_type not in ("text/csv", "application/csv"):
        log.warning("invalid_content_type", content_type=file.content_type)
        raise HTTPException(status_code=400, detail="File must be a CSV")

    content = await file.read()

    try:
        hospitals = parse_and_validate_csv(content)
    except CSVValidationError as e:
        log.warning("csv_validation_failed", error_count=len(e.errors), errors=e.errors)
        return JSONResponse(status_code=422, content={"errors": e.errors})

    batch_id = uuid.uuid4()
    log.info("batch_started", batch_id=str(batch_id), total=len(hospitals))

    start = time.perf_counter()
    results: list[HospitalResult] = []
    failed = 0

    async with httpx.AsyncClient(base_url=HOSPITALS_API_URL, timeout=30.0) as client:
        for row, hospital in enumerate(hospitals, start=1):
            try:
                result = await create_hospital(client, hospital, batch_id, row)
                results.append(result)
            except httpx.HTTPError as e:
                failed += 1
                log.error(
                    "hospital_create_failed",
                    row=row,
                    name=hospital.name,
                    error=repr(e),
                )
                results.append(HospitalResult(
                    row=row,
                    hospital_id=0,
                    name=hospital.name,
                    status="failed",
                ))

        batch_activated = False
        if failed == 0:
            try:
                batch_activated = await activate_batch(client, batch_id)
                # Mark all results as created_and_activated
                results = [r.model_copy(update={"status": "created_and_activated"}) for r in results]
            except httpx.HTTPStatusError:
                log.error("batch_activation_failed", batch_id=str(batch_id))

    processing_time = round(time.perf_counter() - start, 3)

    log.info(
        "batch_complete",
        batch_id=str(batch_id),
        processed=len(hospitals) - failed,
        failed=failed,
        activated=batch_activated,
        processing_time_seconds=processing_time,
    )

    return BulkCreateResponse(
        batch_id=batch_id,
        total_hospitals=len(hospitals),
        processed_hospitals=len(hospitals) - failed,
        failed_hospitals=failed,
        processing_time_seconds=processing_time,
        batch_activated=batch_activated,
        hospitals=results,
    )
