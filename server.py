from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from internal.validation.csv import parse_and_validate_csv, CSVValidationError

limiter = Limiter(key_func=get_remote_address)

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/hospitals/bulk")
@limiter.limit("10/minute")
async def bulk_create_hospitals(request: Request, file: UploadFile = File(...)):
    if file.content_type not in ("text/csv", "application/csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    content = await file.read()

    try:
        hospitals = parse_and_validate_csv(content)
    except CSVValidationError as e:
        return JSONResponse(status_code=422, content={"errors": e.errors})

    return {"count": len(hospitals), "hospitals": [h.model_dump() for h in hospitals]}
