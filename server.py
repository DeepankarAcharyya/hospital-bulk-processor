import time

import structlog
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from internal.logging_config import configure_logging
from internal.models.bulk_response import BulkCreateResponse
from internal.validation.csv import parse_and_validate_csv, CSVValidationError

configure_logging()

log = structlog.get_logger(__name__)

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

    log.info("csv_parsed", hospital_count=len(hospitals))

    # TODO: persist hospitals, populate batch_id, hospital_id, processing_time_seconds
    pass
