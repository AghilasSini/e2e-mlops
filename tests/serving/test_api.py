"""One test per scenario of specs/prediction-api, against a really trained model."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data.features import FEATURE_COLUMNS
from serving.app import create_app

SAMPLE = {
    "fixed_acidity": 7.4,
    "volatile_acidity": 0.7,
    "citric_acid": 0.0,
    "residual_sugar": 1.9,
    "chlorides": 0.076,
    "free_sulfur_dioxide": 11.0,
    "total_sulfur_dioxide": 34.0,
    "density": 0.9978,
    "ph": 3.51,
    "sulphates": 0.56,
    "alcohol": 9.4,
}
# A high-alcohol, low-volatile-acidity wine (quality 7 in the dataset).
GOOD_SAMPLE = {**SAMPLE, "volatile_acidity": 0.3, "alcohol": 12.8, "sulphates": 0.8}


@pytest.fixture()
def client(trained_model_dir: Path) -> Iterator[TestClient]:
    app = create_app(str(trained_model_dir / "model.joblib"))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture()
def empty_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(str(tmp_path / "missing.joblib"))) as test_client:
        yield test_client


def _invalid_fields(response) -> dict[str, str]:
    return {e["loc"][-1]: e["type"] for e in response.json()["detail"]}


# Scenario: Valid request
def test_valid_request(client: TestClient) -> None:
    response = client.post("/predict", json=SAMPLE)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"prediction", "label", "probability", "model_version"}
    assert body["prediction"] in (0, 1)
    assert body["label"] == ("good" if body["prediction"] == 1 else "not_good")
    assert 0.0 <= body["probability"] <= 1.0
    assert body["model_version"] == "test-model-1"


def test_known_samples_get_sensible_labels(client: TestClient) -> None:
    assert client.post("/predict", json=SAMPLE).json()["label"] == "not_good"
    assert client.post("/predict", json=GOOD_SAMPLE).json()["label"] == "good"


# Scenario: Batch request
def test_batch_request_keeps_order(client: TestClient) -> None:
    response = client.post("/predict", json={"instances": [SAMPLE, GOOD_SAMPLE, SAMPLE]})
    assert response.status_code == 200
    body = response.json()
    labels = [p["label"] for p in body["predictions"]]
    assert labels == ["not_good", "good", "not_good"]
    assert body["model_version"] == "test-model-1"


# Scenario: Missing and malformed fields
def test_missing_and_malformed_fields(client: TestClient) -> None:
    response = client.post("/predict", json={"alcohol": "abc"})
    assert response.status_code == 422
    fields = _invalid_fields(response)
    assert fields.pop("alcohol") != "missing"
    assert set(fields) == set(FEATURE_COLUMNS) - {"alcohol"}
    assert set(fields.values()) == {"missing"}


# Scenario: Unknown field
def test_unknown_field(client: TestClient) -> None:
    response = client.post("/predict", json={**SAMPLE, "quality": 5})
    assert response.status_code == 422
    assert _invalid_fields(response) == {"quality": "extra_forbidden"}


def test_negative_value(client: TestClient) -> None:
    response = client.post("/predict", json={**SAMPLE, "density": -1})
    assert response.status_code == 422
    assert _invalid_fields(response) == {"density": "greater_than_equal"}


def test_invalid_batch_item_is_located(client: TestClient) -> None:
    response = client.post("/predict", json={"instances": [SAMPLE, {**SAMPLE, "ph": "x"}]})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "instances", 1, "ph"]


@pytest.mark.parametrize("body", [[SAMPLE], "text", 3])
def test_non_object_body(client: TestClient, body) -> None:
    assert client.post("/predict", json=body).status_code == 422


def test_malformed_json(client: TestClient) -> None:
    response = client.post(
        "/predict", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422


# Scenario: Model failure
def test_model_failure_is_500_and_logged(client: TestClient, caplog) -> None:
    class Broken:
        def predict_proba(self, _):
            raise RuntimeError("boom inside the model")

    client.app.state.store._model = Broken()
    with caplog.at_level(logging.ERROR):
        response = client.post("/predict", json=SAMPLE)
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert "boom" not in response.text
    assert "boom inside the model" in caplog.text


# Scenario: No model available
def test_no_model_available(empty_client: TestClient) -> None:
    assert empty_client.get("/health/live").status_code == 200
    ready = empty_client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "unavailable"
    assert empty_client.post("/predict", json=SAMPLE).status_code == 503


def test_ready_with_model(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model_version": "test-model-1"}


def test_corrupt_model_keeps_service_alive(tmp_path: Path) -> None:
    broken = tmp_path / "model.joblib"
    broken.write_bytes(b"not a pickle")
    with TestClient(create_app(str(broken))) as test_client:
        assert test_client.get("/health/live").status_code == 200
        assert test_client.get("/health/ready").status_code == 503


# Scenario: Repeated predictions
def test_model_loaded_once_for_100_predictions(client: TestClient) -> None:
    for _ in range(100):
        assert client.post("/predict", json=SAMPLE).status_code == 200
    assert client.app.state.store.load_count == 1


# Scenario: Former training route
@pytest.mark.parametrize("method", ["get", "post"])
def test_no_training_route(client: TestClient, method: str) -> None:
    assert getattr(client, method)("/train").status_code == 404


def test_no_route_runs_commands(client: TestClient) -> None:
    # Included routers (the /v2 protocol) are not plain routes: read them from OpenAPI.
    paths = {route.path for route in client.app.routes if hasattr(route, "path")}
    paths |= set(client.app.openapi()["paths"])
    assert paths <= {
        "/v2",
        "/v2/health/live",
        "/v2/health/ready",
        "/v2/models/{name}",
        "/v2/models/{name}/ready",
        "/v2/models/{name}/infer",
        "/",
        "/predict",
        "/health/live",
        "/health/ready",
        "/static",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }


# Scenario: Form submission
def test_form_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    for name in FEATURE_COLUMNS:
        assert f'name="{name}"' in response.text


def test_form_submission_shows_label_and_probability(client: TestClient) -> None:
    response = client.post("/", data={k: str(v) for k, v in GOOD_SAMPLE.items()})
    assert response.status_code == 200
    assert ">good<" in response.text
    assert "Probability of a good wine" in response.text
    assert "test-model-1" in response.text


def test_form_uses_api_validation(client: TestClient) -> None:
    response = client.post("/", data={**{k: str(v) for k, v in SAMPLE.items()}, "alcohol": "abc"})
    assert response.status_code == 422
    assert 'class="invalid"' in response.text
    assert 'value="abc"' in response.text


def test_static_assets_are_served(client: TestClient) -> None:
    assert client.get("/static/style.css").status_code == 200


def test_model_uri_from_environment(trained_model_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_URI", str(trained_model_dir / "model.joblib"))
    with TestClient(create_app()) as test_client:
        assert test_client.get("/health/ready").status_code == 200


def test_openapi_documents_predict(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/predict" in spec["paths"]
    assert "/train" not in spec["paths"]


def test_readiness_recovers_when_model_appears(
    trained_model_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    import serving.app as app_module

    monkeypatch.setattr(app_module, "RELOAD_INTERVAL", 0.0)
    target = tmp_path / "model.joblib"
    with TestClient(create_app(str(target))) as client:
        assert client.get("/health/ready").status_code == 503
        target.write_bytes((trained_model_dir / "model.joblib").read_bytes())
        (tmp_path / "model_version").write_text("late-model\n")
        ready = client.get("/health/ready")
        assert ready.status_code == 200 and ready.json()["model_version"] == "late-model"
        assert client.post("/predict", json=SAMPLE).status_code == 200
