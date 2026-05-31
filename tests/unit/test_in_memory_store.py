import pytest
from pydantic import ValidationError

from internal.models.batch import BatchState, BatchStatus
from internal.store.in_memory import InMemoryStore


def _state(batch_id: str = "batch-1") -> BatchState:
    return BatchState(batch_id=batch_id, status=BatchStatus.ACCEPTED, total_hospitals=3)


def test_get_unknown_returns_none():
    store = InMemoryStore()
    assert store.get("unknown") is None


def test_set_then_get_returns_state():
    store = InMemoryStore()
    state = _state()
    store.set("batch-1", state)
    assert store.get("batch-1") == state


def test_update_partial_fields():
    store = InMemoryStore()
    store.set("batch-1", _state())
    store.update("batch-1", status=BatchStatus.PROCESSING, processed_hospitals=2)
    result = store.get("batch-1")
    assert result.status == BatchStatus.PROCESSING
    assert result.processed_hospitals == 2
    assert result.total_hospitals == 3  # unchanged


def test_update_unknown_key_raises():
    store = InMemoryStore()
    with pytest.raises(KeyError):
        store.update("nonexistent", status=BatchStatus.FAILED)


def test_update_unknown_field_raises():
    store = InMemoryStore()
    store.set("batch-1", _state())
    with pytest.raises(ValidationError):
        store.update("batch-1", nonexistent_field="bad")


def test_get_returns_independent_copy():
    store = InMemoryStore()
    store.set("batch-1", _state())
    result = store.get("batch-1")
    result.status = BatchStatus.FAILED  # mutate the returned copy
    # stored state should be unchanged
    assert store.get("batch-1").status == BatchStatus.ACCEPTED
