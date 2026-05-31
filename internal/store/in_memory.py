from internal.models.batch import BatchState


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, BatchState] = {}

    def set(self, batch_id: str, state: BatchState) -> None:
        self._data[batch_id] = state

    def get(self, batch_id: str) -> BatchState | None:
        state = self._data.get(batch_id)
        return state.model_copy() if state is not None else None

    def update(self, batch_id: str, **kwargs) -> None:
        state = self._data[batch_id]  # raises KeyError if missing
        self._data[batch_id] = BatchState.model_validate({**state.model_dump(), **kwargs})
