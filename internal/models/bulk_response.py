from uuid import UUID

from pydantic import BaseModel


class HospitalResult(BaseModel):
    row: int
    hospital_id: int | None
    name: str
    address: str
    phone: str | None = None
    status: str


class BulkCreateResponse(BaseModel):
    batch_id: UUID
    total_hospitals: int
    processed_hospitals: int
    failed_hospitals: int
    processing_time_seconds: float
    batch_activated: bool
    hospitals: list[HospitalResult]
