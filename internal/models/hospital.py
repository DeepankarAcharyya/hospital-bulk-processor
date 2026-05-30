from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class Hospital(BaseModel):
    id: Optional[int] = None
    name: str
    address: str
    phone: Optional[str] = None
    creation_batch_id: Optional[UUID] = None
    active: bool = False
    created_at: Optional[datetime] = None
