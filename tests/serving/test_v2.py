"""specs/prediction-api "Open Inference Protocol v2", against a really trained model."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data.features import FEATURE_COLUMNS
from serving.app import create_app
from serving.schemas import MAX_BATCH_SIZE

INFER = "/v2/models/wine-quality/infer"
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
GOOD_SAMPLE = {**SAMPLE, "volatile_acidity": 0.3, "alcohol": 12.8, "sulphates": 0.8}


def oip(*rows: dict[str, Any], datatype: str = "FP64") -> dict[str, Any]:
    """An OIP v2 infer request with one named input per feature."""
    return {
        "inputs": [
            {"name": f, "shape": [len(rows)], "datatype": datatype, "data": [r[f] for r in rows]}
            for f in FEATURE_COLUMNS
        ]
    }


def without(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return {"inputs": [i for i in payload["inputs"] if i["name"] != name]}


def outputs(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["name"]: o for o in body["outputs"]}


@pytest.fixture()
def client(trained_model_dir: Path) -> Iterator[TestClient]:
    app = create_app(str(trained_model_dir / "model.joblib"), model_name="wine-quality")
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture()
def empty_client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(str(tmp_path / "missing.joblib"), model_name="wine-quality")
    with TestClient(app) as test_client:
        yield test_client


# Scenario: Valid inference request
def test_valid_inference_request(client: TestClient) -> None:
    response = client.post(INFER, json=oip(SAMPLE, GOOD_SAMPLE))
    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "wine-quality"
    assert body["model_version"] == "test-model-1"
    out = outputs(body)
    assert set(out) == {"prediction", "probability"}
    assert out["prediction"]["datatype"] == "INT64" and out["prediction"]["shape"] == [2]
    assert out["probability"]["datatype"] == "FP64" and out["probability"]["shape"] == [2]
    assert out["prediction"]["data"] == [0, 1]
    assert all(0.0 <= p <= 1.0 for p in out["probability"]["data"])


def test_same_answers_as_the_json_api(client: TestClient) -> None:
    v2 = outputs(client.post(INFER, json=oip(SAMPLE, GOOD_SAMPLE)).json())
    v1 = client.post("/predict", json={"instances": [SAMPLE, GOOD_SAMPLE]}).json()["predictions"]
    assert v2["prediction"]["data"] == [p["prediction"] for p in v1]
    assert v2["probability"]["data"] == pytest.approx([p["probability"] for p in v1])


def test_request_id_is_echoed(client: TestClient) -> None:
    body = client.post(INFER, json={**oip(SAMPLE), "id": "req-42"}).json()
    assert body["id"] == "req-42"
    assert client.post(INFER, json=oip(SAMPLE)).json()["id"]  # generated when absent


def test_nested_row_major_data_is_accepted(client: TestClient) -> None:
    payload = oip(SAMPLE, GOOD_SAMPLE)
    for item in payload["inputs"]:
        item["shape"], item["data"] = [2, 1], [[v] for v in item["data"]]
    assert outputs(client.post(INFER, json=payload).json())["prediction"]["data"] == [0, 1]


def test_requested_outputs_only(client: TestClient) -> None:
    payload = {**oip(SAMPLE), "outputs": [{"name": "probability"}]}
    assert set(outputs(client.post(INFER, json=payload).json())) == {"probability"}
    payload["outputs"] = [{"name": "label"}]
    response = client.post(INFER, json=payload)
    assert response.status_code == 400 and "label" in response.json()["error"]


# Scenario: Missing input
def test_missing_input(client: TestClient) -> None:
    response = client.post(INFER, json=without(oip(SAMPLE), "alcohol"))
    assert response.status_code == 400
    assert set(response.json()) == {"error"}
    assert "alcohol" in response.json()["error"]


def test_unequal_input_lengths(client: TestClient) -> None:
    payload = oip(SAMPLE, GOOD_SAMPLE)
    payload["inputs"][0] = {**payload["inputs"][0], "shape": [1], "data": [7.4]}
    response = client.post(INFER, json=payload)
    assert response.status_code == 400
    assert "same number of values" in response.json()["error"]
    assert "fixed_acidity=1" in response.json()["error"]


def test_shape_must_match_data(client: TestClient) -> None:
    payload = oip(SAMPLE)
    payload["inputs"][3]["shape"] = [2]
    response = client.post(INFER, json=payload)
    assert response.status_code == 400 and "residual_sugar" in response.json()["error"]


@pytest.mark.parametrize("datatype", ["FP32", "INT64", "BYTES", None])
def test_wrong_datatype(client: TestClient, datatype: str | None) -> None:
    payload = oip(SAMPLE)
    payload["inputs"][-1]["datatype"] = datatype
    response = client.post(INFER, json=payload)
    assert response.status_code == 400
    assert "alcohol" in response.json()["error"] and "FP64" in response.json()["error"]


def test_unknown_input(client: TestClient) -> None:
    payload = oip(SAMPLE)
    payload["inputs"].append({"name": "colour", "shape": [1], "datatype": "FP64", "data": [1.0]})
    response = client.post(INFER, json=payload)
    assert response.status_code == 400 and "colour" in response.json()["error"]


def test_duplicate_input(client: TestClient) -> None:
    payload = oip(SAMPLE)
    payload["inputs"].append(dict(payload["inputs"][0]))
    response = client.post(INFER, json=payload)
    assert response.status_code == 400 and "more than once" in response.json()["error"]


@pytest.mark.parametrize(
    ("value", "message"),
    [(-1.0, "greater than or equal to 0"), ("abc", "valid number"), (True, "valid number")],
)
def test_same_validation_as_predict(client: TestClient, value: Any, message: str) -> None:
    payload = oip(SAMPLE, GOOD_SAMPLE)
    payload["inputs"][-1]["data"][1] = value
    response = client.post(INFER, json=payload)
    assert response.status_code == 400
    error = response.json()["error"]
    assert "'alcohol' value 1" in error and message in error


@pytest.mark.parametrize(
    "payload",
    [[], {"inputs": "x"}, {"inputs": [{"shape": [1]}]}, {"inputs": [{"name": "alcohol"}]}],
)
def test_malformed_envelope(client: TestClient, payload: Any) -> None:
    response = client.post(INFER, json=payload)
    assert response.status_code == 400 and response.json()["error"]


def test_empty_and_oversized_batches(client: TestClient) -> None:
    assert client.post(INFER, json=oip()).status_code == 400
    response = client.post(INFER, json=oip(*[SAMPLE] * (MAX_BATCH_SIZE + 1)))
    assert response.status_code == 400 and str(MAX_BATCH_SIZE) in response.json()["error"]


def test_unknown_model_is_404(client: TestClient) -> None:
    for response in (
        client.post("/v2/models/other/infer", json=oip(SAMPLE)),
        client.get("/v2/models/other"),
        client.get("/v2/models/other/ready"),
    ):
        assert response.status_code == 404 and "other" in response.json()["error"]


# Scenario: Readiness
def test_readiness_without_model(empty_client: TestClient) -> None:
    assert empty_client.get("/v2/health/live").json() == {"live": True}
    assert empty_client.get("/v2/health/ready").status_code != 200
    assert empty_client.get("/v2/models/wine-quality/ready").status_code != 200
    response = empty_client.post(INFER, json=oip(SAMPLE))
    assert response.status_code == 503 and response.json() == {"error": "model not loaded"}


def test_readiness_with_model(client: TestClient) -> None:
    assert client.get("/v2/health/ready").json() == {"ready": True}
    assert client.get("/v2/models/wine-quality/ready").json() == {
        "name": "wine-quality",
        "ready": True,
    }


def test_server_and_model_metadata(client: TestClient) -> None:
    server = client.get("/v2").json()
    assert server["name"] and server["version"] == "0.2.0"
    meta = client.get("/v2/models/wine-quality").json()
    assert meta["name"] == "wine-quality"
    assert meta["versions"] == ["test-model-1"]
    assert [i["name"] for i in meta["inputs"]] == list(FEATURE_COLUMNS)
    assert {i["datatype"] for i in meta["inputs"]} == {"FP64"}
    assert {o["name"]: o["datatype"] for o in meta["outputs"]} == {
        "prediction": "INT64",
        "probability": "FP64",
    }


def test_default_model_name_comes_from_the_registry_config(trained_model_dir: Path) -> None:
    with TestClient(create_app(str(trained_model_dir / "model.joblib"))) as c:
        assert c.get("/v2/models/wine-quality/ready").status_code == 200
