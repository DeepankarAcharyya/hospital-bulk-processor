import csv
import io

from pydantic import ValidationError

from internal.models.hospital import Hospital


# Hard limit per business requirement
MAX_ROWS = 20
REQUIRED_COLUMNS = {"name", "address"}
ALLOWED_COLUMNS = {"name", "address", "phone"}


class CSVValidationError(Exception):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s)")


def parse_and_validate_csv(content: bytes) -> list[Hospital]:
    """Parse CSV bytes and return validated Hospital objects.

    Raises CSVValidationError with all row-level errors collected (not fail-fast).
    """
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise CSVValidationError([{"row": 0, "error": "Empty file"}])

    # Normalize headers to lowercase to tolerate minor formatting differences
    headers = {h.strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_COLUMNS - headers
    if missing:
        raise CSVValidationError([{"row": 0, "error": f"Missing columns: {missing}"}])

    # Materialize rows before iterating so we can check count upfront
    all_rows = list(reader)
    if len(all_rows) > MAX_ROWS:
        raise CSVValidationError([{"row": 0, "error": f"CSV exceeds maximum of {MAX_ROWS} hospitals"}])

    rows: list[Hospital] = []
    errors: list[dict] = []

    for i, raw in enumerate(all_rows, start=1):
        # Strip whitespace and drop unknown columns before validation
        row = {k.strip().lower(): v.strip() for k, v in raw.items() if k}
        try:
            rows.append(Hospital(**{k: v or None for k, v in row.items() if k in ALLOWED_COLUMNS}))
        except ValidationError as e:
            errors.append({"row": i, "error": e.errors()})

    if errors:
        raise CSVValidationError(errors)

    return rows
