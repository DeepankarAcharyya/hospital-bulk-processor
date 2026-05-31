from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from internal.models.bulk_response import HospitalResult


class BatchStatus(str, Enum):
    ACCEPTED   = "accepted"
    PROCESSING = "processing"
    CREATED    = "created"
    COOLDOWN   = "cooldown"
    COMPLETED  = "created_and_activated"
    FAILED     = "failed"


class BatchState(BaseModel):
    model_config = ConfigDict(extra='forbid')

    batch_id: str
    status: BatchStatus
    total_hospitals: int
    processed_hospitals: int = 0
    failed_hospitals: int = 0
    processing_time_seconds: float = 0.0
    batch_activated: bool = False
    hospitals: list[HospitalResult] = Field(default_factory=list)
    error_message: str | None = None
