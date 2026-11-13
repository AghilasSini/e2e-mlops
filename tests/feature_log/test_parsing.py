"""feature_log.events and feature_log.parsing on the CloudEvents KServe really sends."""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any

import pytest

from data.features import FEATURE_COLUMNS as MODEL_FEATURES
from feature_log.events import EventError, parse_event
from feature_log.parsing import FEATURES, parse_request, parse_response


def event_of(captured: dict[str, dict[str, Any]], name: str):
    raw = captured[name]
    return parse_event(raw["headers"], raw["body"].encode())


def test_feature_names_match_the_model() -> None:
    assert FEATURES == frozenset(MODEL_FEATURES)


# ------------------------------------------------------------------ CloudEvents
def test_captured_headers(captured) -> None:
    request = event_of(captured, "predict-single-request")
    response = event_of(captured, "predict-single-response")
    assert request.kind == "request" and response.kind == "response"
    assert request.event_id == response.event_id
    assert (request.inference_service, request.namespace, request.component) == (
        "spike",
        "spike",
        "predictor",
    )
    assert request.time.tzinfo == timezone.utc
    assert request.time.isoformat().startswith("2026-11-11T13:00:05.175431")


def test_headers_are_case_insensitive() -> None:
    event = parse_event({"ce-id": "a", "CE-TYPE": "org.kubeflow.serving.inference.response"}, b"{}")
    assert event.event_id == "a" and event.kind == "response"
    assert event.inference_service is None


@pytest.mark.parametrize(
    "headers",
    [
        {"Ce-Type": "org.kubeflow.serving.inference.request"},
        {"Ce-Id": " ", "Ce-Type": "org.kubeflow.serving.inference.request"},
        {"Ce-Id": "a", "Ce-Type": "com.example.other"},
        {"Ce-Id": "a", "Ce-Type": "org.kubeflow.serving.inference.explain"},
        {"Ce-Id": "a"},
    ],
)
def test_not_an_inference_event(headers: dict[str, str]) -> None:
    with pytest.raises(EventError):
        parse_event(headers, b"{}")


def test_bad_or_missing_time_falls_back_to_now() -> None:
    for time in ("yesterday", ""):
        event = parse_event(
            {"Ce-Id": "a", "Ce-Type": "org.kubeflow.serving.inference.request", "Ce-Time": time},
            b"",
        )
        assert event.time.tzinfo is not None


def test_undecodable_body_is_kept() -> None:
    event = parse_event(
        {"Ce-Id": "a", "Ce-Type": "org.kubeflow.serving.inference.request"}, b"\xff"
    )
    assert event.body == "�"


# ------------------------------------------------------------------ JSON API
def test_single(captured) -> None:
    request = parse_request(event_of(captured, "predict-single-request").body)
    response = parse_response(event_of(captured, "predict-single-response").body)
    assert request is not None and len(request.rows) == 1
    assert request.rows[0]["alcohol"] == 9.4 and set(request.rows[0]) == FEATURES
    assert response is not None and response.model_version == "spike/1"
    [prediction] = response.predictions
    assert prediction.prediction == 0 and prediction.probability == pytest.approx(0.2166816)


def test_batch(captured) -> None:
    request = parse_request(event_of(captured, "predict-batch-request").body)
    response = parse_response(event_of(captured, "predict-batch-response").body)
    assert request is not None and response is not None
    assert [r["fixed_acidity"] for r in request.rows] == [7.4, 7.8, 11.2]  # request order
    assert [p.prediction for p in response.predictions] == [0, 0, 1]
    assert response.model_version == "spike/1"


# ------------------------------------------------------------------ OIP v2
def test_oip(captured) -> None:
    request = parse_request(event_of(captured, "oip-infer-request").body)
    response = parse_response(event_of(captured, "oip-infer-response").body)
    assert request is not None and len(request.rows) == 2
    assert [r["alcohol"] for r in request.rows] == [9.4, 9.8]
    assert response is not None and response.model_version == "spike/1"
    assert [p.prediction for p in response.predictions] == [0, 1]
    assert response.predictions[1].probability == pytest.approx(0.5329660)


def test_oip_rejected_request_is_unparsed(captured) -> None:
    assert parse_request(event_of(captured, "oip-400-request").body) is None  # no alcohol


def test_oip_nested_data_and_partial_outputs() -> None:
    request = {"inputs": [{"name": f, "shape": [2, 1], "data": [[1.0], [2.0]]} for f in FEATURES]}
    parsed = parse_request(json.dumps(request))
    assert parsed is not None and [r["ph"] for r in parsed.rows] == [1.0, 2.0]
    response = {
        "model_version": "wine-quality/2",
        "outputs": [{"name": "probability", "data": [0.7]}],
    }
    only_probability = parse_response(json.dumps(response))
    assert only_probability is not None
    assert only_probability.predictions[0].prediction is None
    assert only_probability.predictions[0].probability == 0.7


# ------------------------------------------------------------------ unparsed
def test_422_body(captured) -> None:
    event = event_of(captured, "predict-422-request")
    assert event.body == '{"alcohol":"abc"}'
    assert parse_request(event.body) is None
    assert "predict-422-response" not in captured  # KServe sends none for an error


def test_empty_body(captured) -> None:
    event = event_of(captured, "get-ready-request")
    assert event.body == ""
    assert parse_request(event.body) is None


def test_health_answer_is_not_a_prediction(captured) -> None:
    assert parse_response(event_of(captured, "get-ready-response").body) is None
    assert parse_response(event_of(captured, "get-live-response").body) is None


ROW = {f: 1.0 for f in FEATURES}


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[1, 2]",
        json.dumps({**ROW, "colour": 1.0}),
        json.dumps({**ROW, "ph": "3.5"}),
        json.dumps({**ROW, "ph": True}),
        json.dumps({k: v for k, v in ROW.items() if k != "ph"}),
        json.dumps({"instances": []}),
        json.dumps({"instances": [ROW], "extra": 1}),
        json.dumps({"instances": [ROW, {"ph": 1}]}),
        json.dumps({"inputs": [{"name": f, "data": [1.0]} for f in FEATURES if f != "ph"]}),
        json.dumps(
            {"inputs": [{"name": f, "data": [1.0] if f != "ph" else [1, 2]} for f in FEATURES]}
        ),
        json.dumps({"inputs": [{"name": f, "data": []} for f in FEATURES]}),
        json.dumps({"inputs": "x"}),
        "fixed_acidity=7.4&alcohol=9.4",  # the HTML form
    ],
)
def test_unparsed_requests(body: str) -> None:
    assert parse_request(body) is None


@pytest.mark.parametrize(
    "body",
    [
        "",
        "<html></html>",
        json.dumps({"prediction": 2, "probability": 0.5}),
        json.dumps({"prediction": True, "probability": 0.5}),
        json.dumps({"prediction": 1}),
        json.dumps({"predictions": [{"prediction": 1, "probability": 0.9}, {}]}),
        json.dumps({"predictions": "x"}),
        json.dumps({"outputs": [{"name": "label", "data": ["good"]}]}),
        json.dumps(
            {
                "outputs": [
                    {"name": "prediction", "data": [1, 0]},
                    {"name": "probability", "data": [0.9]},
                ]
            }
        ),
        json.dumps({"outputs": [{"name": "prediction", "data": [3]}]}),
        json.dumps({"outputs": [{"name": "probability", "data": ["x"]}]}),
        json.dumps({"detail": "internal server error"}),
    ],
)
def test_unparsed_responses(body: str) -> None:
    assert parse_response(body) is None
