import io
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from internal.models.bulk_response import HospitalResult
from tests.conftest import valid_csv, make_csv


def upload_csv(content: bytes, content_type: str = "text/csv"):
    return {"file": ("hospitals.csv", io.BytesIO(content), content_type)}


def make_hospital_result(row: int, name: str) -> HospitalResult:
    return HospitalResult(
        row=row,
        hospital_id=100 + row,
        name=name,
        status="created_and_activated",
    )


@pytest.fixture
def mock_http_calls(mocker):
    """Patch create_hospital and activate_batch in server module."""
    create = mocker.patch("server.create_hospital", new_callable=AsyncMock)
    create.side_effect = lambda client, hospital, batch_id, row: make_hospital_result(row, hospital.name)

    activate = mocker.patch("server.activate_batch", new_callable=AsyncMock, return_value=True)

    return create, activate


class TestBulkCreateHospitals:
    def test_valid_csv_returns_200(self, client, mock_http_calls):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(2)))
        assert response.status_code == 200

    def test_valid_csv_response_shape(self, client, mock_http_calls):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(2)))
        body = response.json()
        assert body["total_hospitals"] == 2
        assert body["processed_hospitals"] == 2
        assert body["failed_hospitals"] == 0
        assert body["batch_activated"] is True
        assert len(body["hospitals"]) == 2

    def test_valid_csv_batch_id_is_uuid(self, client, mock_http_calls):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(1)))
        batch_id = response.json()["batch_id"]
        uuid.UUID(batch_id)  # raises if invalid

    def test_hospital_results_have_expected_fields(self, client, mock_http_calls):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(1)))
        hospital = response.json()["hospitals"][0]
        assert "row" in hospital
        assert "hospital_id" in hospital
        assert "name" in hospital
        assert "status" in hospital

    def test_activate_called_once_on_success(self, client, mock_http_calls):
        _, activate = mock_http_calls
        client.post("/hospitals/bulk", files=upload_csv(valid_csv(3)))
        activate.assert_awaited_once()

    def test_invalid_content_type_returns_400(self, client):
        response = client.post(
            "/hospitals/bulk",
            files=upload_csv(valid_csv(1), content_type="application/json"),
        )
        assert response.status_code == 400

    def test_missing_required_column_returns_422(self, client):
        content = make_csv(["address,phone", "123 Main St,555-1234"])
        response = client.post("/hospitals/bulk", files=upload_csv(content))
        assert response.status_code == 422
        assert "errors" in response.json()

    def test_exceeds_max_rows_returns_422(self, client):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(21)))
        assert response.status_code == 422

    def test_create_failure_skips_activation(self, client, mocker):
        mocker.patch("server.create_hospital", new_callable=AsyncMock,
                     side_effect=httpx.HTTPError("upstream error"))
        activate = mocker.patch("server.activate_batch", new_callable=AsyncMock)

        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(1)))

        assert response.status_code == 200
        assert response.json()["batch_activated"] is False
        activate.assert_not_awaited()

    def test_create_failure_reflected_in_counts(self, client, mocker):
        mocker.patch("server.create_hospital", new_callable=AsyncMock,
                     side_effect=httpx.HTTPError("upstream error"))
        mocker.patch("server.activate_batch", new_callable=AsyncMock)

        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(2)))
        body = response.json()

        assert body["failed_hospitals"] == 2
        assert body["processed_hospitals"] == 0
