from uuid import UUID

from pydantic import BaseModel


class HospitalResult(BaseModel):
    row: int
    hospital_id: int
    name: str
    status: str


class BulkCreateResponse(BaseModel):
    batch_id: UUID
    total_hospitals: int
    processed_hospitals: int
    failed_hospitals: int
    processing_time_seconds: float
    batch_activated: bool
    hospitals: list[HospitalResult]
