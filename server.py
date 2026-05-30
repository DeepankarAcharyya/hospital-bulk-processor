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

from internal.clients.hospital_client import create_hospital, activate_batch
from internal.logging_config import configure_logging
from internal.models.bulk_response import BulkCreateResponse, HospitalResult
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

    async with httpx.AsyncClient(base_url=HOSPITALS_API_URL) as client:
        for row, hospital in enumerate(hospitals, start=1):
            try:
                result = await create_hospital(client, hospital, batch_id, row)
                results.append(result)
            except httpx.HTTPStatusError as e:
                failed += 1
                log.error(
                    "hospital_create_failed",
                    row=row,
                    name=hospital.name,
                    status_code=e.response.status_code,
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
