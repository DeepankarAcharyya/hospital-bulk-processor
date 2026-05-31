from internal.models.batch import BatchState, BatchStatus
from internal.models.bulk_response import HospitalResult
from internal.models.hospital import Hospital


def initial_hospital_results(hospitals: list[Hospital]) -> list[HospitalResult]:
    return [
        HospitalResult(
            row=row,
            hospital_id=None,
            name=hospital.name,
            address=hospital.address,
            phone=hospital.phone,
            status=BatchStatus.ACCEPTED.value,
        )
        for row, hospital in enumerate(hospitals, start=1)
    ]


def update_hospital_result(
    state: BatchState,
    row: int,
    *,
    status: str,
    hospital_id: int | None = None,
) -> list[HospitalResult]:
    hospitals = [hospital.model_copy(deep=True) for hospital in state.hospitals]
    index = row - 1
    if index < 0 or index >= len(hospitals):
        return hospitals

    current = hospitals[index]
    hospitals[index] = HospitalResult(
        row=current.row,
        hospital_id=hospital_id if hospital_id is not None else current.hospital_id,
        name=current.name,
        address=current.address,
        phone=current.phone,
        status=status,
    )
    return hospitals


def mark_all_hospital_results(state: BatchState, status: str) -> list[HospitalResult]:
    return [
        HospitalResult(
            row=hospital.row,
            hospital_id=hospital.hospital_id,
            name=hospital.name,
            address=hospital.address,
            phone=hospital.phone,
            status=status,
        )
        for hospital in state.hospitals
    ]


def hospitals_from_state(state: BatchState) -> list[Hospital]:
    return [
        Hospital(
            name=hospital.name,
            address=hospital.address,
            phone=hospital.phone,
        )
        for hospital in sorted(state.hospitals, key=lambda hospital: hospital.row)
    ]


def batch_response(state: BatchState) -> dict:
    return state.model_dump(
        mode="json",
        exclude={"hospitals": {"__all__": {"address", "phone"}}},
    )
