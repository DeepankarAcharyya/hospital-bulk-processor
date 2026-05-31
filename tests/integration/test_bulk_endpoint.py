# tests/integration/test_bulk_endpoint.py
import uuid
import io
from tests.conftest import valid_csv, make_csv


def upload_csv(content: bytes, content_type: str = "text/csv"):
    return {"file": ("hospitals.csv", io.BytesIO(content), content_type)}


class TestBulkCreateHospitals:
    def test_valid_csv_returns_202(self, client):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(2)))
        assert response.status_code == 202

    def test_valid_csv_response_has_batch_id(self, client):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(2)))
        assert "batch_id" in response.json()

    def test_valid_csv_batch_id_is_uuid(self, client):
        response = client.post("/hospitals/bulk", files=upload_csv(valid_csv(1)))
        batch_id = response.json()["batch_id"]
        uuid.UUID(batch_id)  # raises if invalid

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
