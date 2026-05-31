import pytest

from internal.validation.csv import parse_and_validate_csv, CSVValidationError, MAX_ROWS


def csv(rows: list[str]) -> bytes:
    return "\n".join(rows).encode()


def test_valid_csv_returns_hospitals():
    content = csv(["name,address,phone", "General Hospital,123 Main St,555-1234"])
    result = parse_and_validate_csv(content)
    assert len(result) == 1
    assert result[0].name == "General Hospital"
    assert result[0].address == "123 Main St"
    assert result[0].phone == "555-1234"


def test_phone_optional_when_missing_from_row():
    content = csv(["name,address", "General Hospital,123 Main St"])
    result = parse_and_validate_csv(content)
    assert result[0].phone is None


def test_phone_optional_when_empty_in_row():
    content = csv(["name,address,phone", "General Hospital,123 Main St,"])
    result = parse_and_validate_csv(content)
    assert result[0].phone is None


def test_missing_name_column_raises_error():
    content = csv(["address,phone", "123 Main St,555-1234"])
    with pytest.raises(CSVValidationError) as exc:
        parse_and_validate_csv(content)
    assert exc.value.errors[0]["row"] == 0
    assert "name" in exc.value.errors[0]["error"]


def test_missing_address_column_raises_error():
    content = csv(["name,phone", "General Hospital,555-1234"])
    with pytest.raises(CSVValidationError) as exc:
        parse_and_validate_csv(content)
    assert "address" in exc.value.errors[0]["error"]


def test_empty_file_raises_error():
    with pytest.raises(CSVValidationError) as exc:
        parse_and_validate_csv(b"")
    assert exc.value.errors[0]["row"] == 0


def test_exceeds_max_rows_raises_error():
    lines = ["name,address"] + [f"Hospital {i},Addr {i}" for i in range(MAX_ROWS + 1)]
    with pytest.raises(CSVValidationError) as exc:
        parse_and_validate_csv(csv(lines))
    assert str(MAX_ROWS) in exc.value.errors[0]["error"]


def test_exactly_max_rows_is_allowed():
    lines = ["name,address"] + [f"Hospital {i},Addr {i}" for i in range(MAX_ROWS)]
    result = parse_and_validate_csv(csv(lines))
    assert len(result) == MAX_ROWS


def test_collects_all_row_errors():
    # name is required so empty name rows should fail
    content = csv(["name,address", ",Addr 1", ",Addr 2"])
    with pytest.raises(CSVValidationError) as exc:
        parse_and_validate_csv(content)
    assert len(exc.value.errors) == 2


def test_strips_whitespace_from_headers_and_values():
    content = csv([" name , address ", " General Hospital , 123 Main St "])
    result = parse_and_validate_csv(content)
    assert result[0].name == "General Hospital"
    assert result[0].address == "123 Main St"


def test_defaults_inactive_active_false():
    content = csv(["name,address", "General Hospital,123 Main St"])
    result = parse_and_validate_csv(content)
    assert result[0].active is False
