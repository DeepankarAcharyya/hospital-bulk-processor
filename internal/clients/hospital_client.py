from uuid import UUID

import httpx
import structlog

from internal.models.hospital import Hospital
from internal.models.bulk_response import HospitalResult

log = structlog.get_logger(__name__)


async def create_hospital(
    client: httpx.AsyncClient,
    hospital: Hospital,
    batch_id: UUID,
    row: int,
) -> HospitalResult:
    """POST /hospitals/ for a single hospital. Returns HospitalResult with status."""
    payload = hospital.model_dump(exclude={"id", "created_at"})
    payload["creation_batch_id"] = str(batch_id)

    response = await client.post("/hospitals/", json=payload)
    response.raise_for_status()

    data = response.json()
    log.info("hospital_created", row=row, hospital_id=data["id"], name=hospital.name)

    return HospitalResult(
        row=row,
        hospital_id=data["id"],
        name=hospital.name,
        address=hospital.address,
        phone=hospital.phone,
        status="created",
    )


async def activate_batch(client: httpx.AsyncClient, batch_id: UUID) -> bool:
    """PATCH /hospitals/batch/{batch_id}/activate. Returns True on success."""
    response = await client.patch(f"/hospitals/batch/{batch_id}/activate")
    response.raise_for_status()

    log.info("batch_activated", batch_id=str(batch_id))
    return True
