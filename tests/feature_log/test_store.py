"""specs/inference-logging scenarios that need no cluster, on SQLite with real events."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from data.columns import FEATURE_COLUMNS
from feature_log.app import create_app, database_url
from feature_log.store import inference_features, inference_log


@pytest.fixture()
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'feature_log.db'}")


@pytest.fixture()
def client(engine: sa.Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=engine)) as test_client:
        yield test_client


def post(client: TestClient, event: dict[str, Any], **header_overrides: str):
    headers = {k: v for k, v in event["headers"].items() if k.lower().startswith("ce-")}
    if "Content-Type" in event["headers"]:
        headers["Content-Type"] = event["headers"]["Content-Type"]
    headers.update(header_overrides)
    return client.post("/", content=event["body"].encode(), headers=headers)


def logs(engine: sa.Engine) -> list[Any]:
    with engine.connect() as conn:
        return conn.execute(sa.select(inference_log)).all()


def features(engine: sa.Engine) -> list[Any]:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(inference_features).order_by(
                inference_features.c.event_id, inference_features.c.row_index
            )
        ).all()


def deliver(client: TestClient, captured, *names: str) -> None:
    for name in names:
        response = post(client, captured[name])
        assert response.status_code == 202, response.text


# Scenario: Single prediction logged
def test_single_prediction(client, engine, captured) -> None:
    deliver(client, captured, "predict-single-request", "predict-single-response")
    [entry] = logs(engine)
    assert entry.event_id == captured["predict-single-request"]["headers"]["Ce-Id"]
    assert entry.request_body == captured["predict-single-request"]["body"]
    assert entry.response_body == captured["predict-single-response"]["body"]
    assert entry.model_version == "spike/1"
    assert entry.inference_service == "spike" and entry.namespace == "spike"
    assert entry.status == "parsed"
    assert entry.received_at.isoformat().startswith("2026-11-11T13:00:05.175431")
    [row] = features(engine)
    assert row.row_index == 0 and row.alcohol == 9.4 and row.fixed_acidity == 7.4
    assert row.prediction == 0 and row.probability == pytest.approx(0.2166816)
    assert row.model_version == "spike/1"
    assert row.received_at == entry.received_at


# Scenario: Batch request
def test_batch_request(client, engine, captured) -> None:
    deliver(client, captured, "predict-batch-request", "predict-batch-response")
    rows = features(engine)
    assert len(rows) == 3 and len({r.event_id for r in rows}) == 1
    assert [r.row_index for r in rows] == [0, 1, 2]
    assert [r.fixed_acidity for r in rows] == [7.4, 7.8, 11.2]  # request order
    assert [r.prediction for r in rows] == [0, 0, 1]
    assert all(r.probability is not None and r.model_version == "spike/1" for r in rows)


# Scenario: Open Inference Protocol request
def test_oip_request(client, engine, captured) -> None:
    deliver(client, captured, "oip-infer-request", "oip-infer-response")
    rows = features(engine)
    assert len(rows) == 2
    for row in rows:
        assert all(getattr(row, name) is not None for name in FEATURE_COLUMNS)
        assert row.prediction in (0, 1) and row.model_version == "spike/1"
    columns = set(rows[0]._mapping)
    assert set(FEATURE_COLUMNS) | {"prediction", "probability", "model_version"} <= columns


# Scenario: Response before request
def test_response_before_request(client, engine, captured) -> None:
    deliver(client, captured, "predict-batch-response")
    early = features(engine)
    assert [r.prediction for r in early] == [0, 0, 1] and early[0].alcohol is None
    deliver(client, captured, "predict-batch-request")
    rows = features(engine)
    assert [r.fixed_acidity for r in rows] == [7.4, 7.8, 11.2]
    assert [r.prediction for r in rows] == [0, 0, 1]
    [entry] = logs(engine)
    assert entry.status == "parsed" and entry.request_body and entry.response_body
    # the call's time is the request's, also on rows the response created first
    assert entry.received_at.isoformat().startswith("2026-11-11T13:00:05.197137")
    assert {r.received_at for r in rows} == {entry.received_at}


# Scenario: Redelivery
def test_redelivery(client, engine, captured) -> None:
    names = ("predict-batch-request", "predict-batch-response")
    deliver(client, captured, *names)
    first = (logs(engine), features(engine))
    deliver(client, captured, *names, *reversed(names))
    assert (logs(engine), features(engine)) == first


def test_request_and_response_together(engine, captured) -> None:
    with TestClient(create_app(engine=engine)) as client:
        names = ("oip-infer-request", "oip-infer-response")
        threads = [threading.Thread(target=deliver, args=(client, captured, n)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    rows = features(engine)
    assert len(rows) == 2 and all(r.prediction is not None and r.ph is not None for r in rows)
    assert logs(engine)[0].status == "parsed"


# Scenario: Validation error logged
def test_unparsed_422(client, engine, captured) -> None:
    deliver(client, captured, "predict-422-request")
    [entry] = logs(engine)
    assert entry.status == "unparsed"
    assert entry.request_body == '{"alcohol":"abc"}' and entry.response_body is None
    assert features(engine) == []


def test_unparsed_oip_400(client, engine, captured) -> None:
    deliver(client, captured, "oip-400-request")
    assert logs(engine)[0].status == "unparsed" and features(engine) == []


def test_unparsed_response_to_a_stored_request_is_kept(client, engine, captured) -> None:
    form = {**captured["predict-422-request"], "body": "fixed_acidity=7.4"}
    deliver_event = post(client, form)
    assert deliver_event.status_code == 202
    answer = {**captured["predict-single-response"], "body": "<html>result</html>"}
    answer["headers"] = {**answer["headers"], "Ce-Id": form["headers"]["Ce-Id"]}
    assert post(client, answer).json() == {"outcome": "stored"}
    [entry] = logs(engine)
    assert entry.response_body == "<html>result</html>" and entry.status == "unparsed"


# Noise filter: empty-body requests (GET through the Service) and their answers
def test_empty_body_dropped(client, engine, captured) -> None:
    for name in (
        "get-live-response",
        "get-live-request",
        "get-ready-request",
        "get-ready-response",
    ):
        response = post(client, captured[name])
        assert response.status_code == 202
    assert logs(engine) == [] and features(engine) == []
    metrics = client.get("/metrics").text
    assert 'feature_log_events_total{outcome="dropped_empty"} 2' in metrics
    assert 'feature_log_events_total{outcome="dropped_not_prediction"} 2' in metrics


def test_not_an_inference_event_is_rejected(client, engine) -> None:
    response = client.post("/", content=b"{}", headers={"ce-id": "x", "ce-type": "other"})
    assert response.status_code == 400
    assert client.post("/", content=b"{}").status_code == 400
    assert 'outcome="rejected"} 2' in client.get("/metrics").text
    assert logs(engine) == []


def test_oversized_body_is_rejected(client, captured) -> None:
    event = {**captured["predict-single-request"], "body": "x" * (1024 * 1024 + 1)}
    assert post(client, event).status_code == 413


def test_metrics_count_stored_events(client, captured) -> None:
    deliver(client, captured, "predict-single-request", "predict-single-response")
    assert 'feature_log_events_total{outcome="stored"} 2' in client.get("/metrics").text


# Failure isolation on the logger side: database down -> not ready, 503, no crash
def test_database_down(tmp_path: Path, captured) -> None:
    missing = sa.create_engine(f"sqlite:///{tmp_path / 'no' / 'such' / 'dir.db'}")
    with TestClient(create_app(engine=missing)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert post(client, captured["predict-single-request"]).status_code == 503
        assert 'outcome="error"} 1' in client.get("/metrics").text


def test_schema_created_once_database_comes_back(tmp_path: Path, captured) -> None:
    db_dir = tmp_path / "later"
    engine = sa.create_engine(f"sqlite:///{db_dir / 'feature_log.db'}")
    with TestClient(create_app(engine=engine)) as client:
        assert client.get("/health/ready").status_code == 503
        db_dir.mkdir()
        assert post(client, captured["predict-single-request"]).status_code == 202
        assert client.get("/health/ready").status_code == 200


def test_health(client) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok"}


# Least-privilege storage: the password comes from its own variable, never logged
def test_database_url_takes_the_password_from_the_secret_variable() -> None:
    url = database_url(
        {
            "FEATURE_LOG_DATABASE_URL": "postgresql+psycopg2://feature_logger@pg:5432/feature_log",
            "FEATURE_LOG_DB_PASSWORD": "s3cret-Value",
        }
    )
    assert url.password == "s3cret-Value" and url.username == "feature_logger"
    assert "s3cret" not in url.render_as_string(hide_password=True)


def test_database_url_is_required() -> None:
    with pytest.raises(RuntimeError, match="FEATURE_LOG_DATABASE_URL"):
        database_url({})


def test_password_never_logged(captured, caplog: pytest.LogCaptureFixture) -> None:
    env = {
        "FEATURE_LOG_DATABASE_URL": "postgresql+psycopg2://feature_logger@127.0.0.1:9/feature_log",
        "FEATURE_LOG_DB_PASSWORD": "s3cret-Value",
    }
    with caplog.at_level(logging.DEBUG):
        with TestClient(create_app(env=env)) as client:  # unreachable: errors are logged
            assert client.get("/health/ready").status_code == 503
            assert post(client, captured["predict-single-request"]).status_code == 503
    assert "feature log database: postgresql+psycopg2://feature_logger:***@" in caplog.text
    assert "s3cret" not in caplog.text


def test_schema_created_once_under_concurrent_first_events(tmp_path: Path, monkeypatch) -> None:
    """Found on the cluster (task 6.1): the database appeared after startup, then a
    request and its response raced to create the tables and one event was lost."""
    from sqlalchemy.exc import IntegrityError

    from feature_log import store as store_module

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'feature_log.db'}")
    feature_store = store_module.FeatureLogStore(engine)
    real_create_all = store_module.metadata.create_all
    calls: list[int] = []

    def racing_create_all(bind):  # the other writer won: first attempt collides
        calls.append(1)
        real_create_all(bind)
        if len(calls) == 1:
            raise IntegrityError("CREATE TABLE inference_log", {}, Exception("duplicate"))

    monkeypatch.setattr(store_module.metadata, "create_all", racing_create_all)
    threads = [threading.Thread(target=feature_store.ensure_schema) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert feature_store.schema_ready
    assert len(calls) == 2  # the colliding attempt and one re-check, nothing more
