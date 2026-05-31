from enum import Enum

from pydantic import BaseModel


class BatchStatus(str, Enum):
    ACCEPTED   = "accepted"
    PROCESSING = "processing"
    COOLDOWN   = "cooldown"
    COMPLETED  = "completed"
    FAILED     = "failed"


class BatchState(BaseModel):
    batch_id: str
    status: BatchStatus
    total_hospitals: int
    processed_hospitals: int = 0
    failed_hospitals: int = 0
    batch_activated: bool = False
    error_message: str | None = None
