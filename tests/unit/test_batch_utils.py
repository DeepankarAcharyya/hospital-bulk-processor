from internal.batch_utils import (
    batch_response,
    hospitals_from_state,
    initial_hospital_results,
    mark_all_hospital_results,
    update_hospital_result,
)
from internal.models.batch import BatchState, BatchStatus
from internal.models.hospital import Hospital


def test_initial_hospital_results_store_raw_fields_for_resume():
    hospitals = [
        Hospital(name="General Hospital", address="1 Main St", phone="555-0001"),
    ]

    results = initial_hospital_results(hospitals)

    assert results[0].row == 1
    assert results[0].hospital_id is None
    assert results[0].name == "General Hospital"
    assert results[0].address == "1 Main St"
    assert results[0].phone == "555-0001"
    assert results[0].status == "accepted"


def test_update_and_mark_hospital_results_preserve_raw_fields():
    state = BatchState(
        batch_id="batch-1",
        status=BatchStatus.ACCEPTED,
        total_hospitals=1,
        hospitals=initial_hospital_results([
            Hospital(name="General Hospital", address="1 Main St", phone=None),
        ]),
    )

    created = update_hospital_result(state, 1, status="created", hospital_id=101)
    activated = mark_all_hospital_results(
        state.model_copy(update={"hospitals": created}),
        "created_and_activated",
    )

    assert activated[0].hospital_id == 101
    assert activated[0].address == "1 Main St"
    assert activated[0].phone is None
    assert activated[0].status == "created_and_activated"


def test_hospitals_from_state_rebuilds_queue_payload_in_row_order():
    state = BatchState(
        batch_id="batch-1",
        status=BatchStatus.FAILED,
        total_hospitals=2,
        hospitals=initial_hospital_results([
            Hospital(name="Hospital B", address="2 Main St", phone=None),
            Hospital(name="Hospital A", address="1 Main St", phone="555-0001"),
        ]),
    )
    state.hospitals[0].row = 2
    state.hospitals[1].row = 1

    hospitals = hospitals_from_state(state)

    assert [hospital.name for hospital in hospitals] == ["Hospital A", "Hospital B"]
    assert hospitals[0].address == "1 Main St"
    assert hospitals[0].phone == "555-0001"


def test_batch_response_excludes_raw_fields():
    state = BatchState(
        batch_id="batch-1",
        status=BatchStatus.ACCEPTED,
        total_hospitals=1,
        hospitals=initial_hospital_results([
            Hospital(name="General Hospital", address="1 Main St", phone="555-0001"),
        ]),
    )

    response = batch_response(state)

    assert response["hospitals"] == [
        {
            "row": 1,
            "hospital_id": None,
            "name": "General Hospital",
            "status": "accepted",
        }
    ]
