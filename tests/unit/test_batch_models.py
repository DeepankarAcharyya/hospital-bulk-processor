from internal.models.batch import BatchState, BatchStatus


def test_batch_state_defaults():
    state = BatchState(batch_id="abc", status=BatchStatus.ACCEPTED, total_hospitals=5)
    assert state.processed_hospitals == 0
    assert state.failed_hospitals == 0
    assert state.processing_time_seconds == 0
    assert state.batch_activated is False
    assert state.hospitals == []
    assert state.error_message is None


def test_batch_status_values():
    assert BatchStatus.ACCEPTED == "accepted"
    assert BatchStatus.PROCESSING == "processing"
    assert BatchStatus.CREATED == "created"
    assert BatchStatus.COOLDOWN == "cooldown"
    assert BatchStatus.COMPLETED == "created_and_activated"
    assert BatchStatus.FAILED == "failed"
