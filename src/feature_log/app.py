"""Feature-logger service: receives KServe logger CloudEvents, stores them.

Endpoints:

* ``POST /``: one CloudEvent (binary mode), answered 202 (stored or deliberately
  dropped), 400 (not an inference event) or 503 (database unavailable)
* ``GET /health/live``, ``GET /health/ready`` (ready = the database answers)
* ``GET /metrics``: event counts by outcome (Prometheus text format)

Settings come from the environment:

``FEATURE_LOG_DATABASE_URL``
    SQLAlchemy URL without the password, e.g.
    ``postgresql+psycopg2://feature_logger@e2e-mlops-postgres:5432/feature_log``
``FEATURE_LOG_DB_PASSWORD``
    the password, from the ``e2e-mlops-feature-log`` Secret (never put in the URL)

Run with ``uvicorn --factory feature_log.app:create_app``. KServe sends events
asynchronously and never waits for this service, so being slow or down cannot affect
predictions.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import SQLAlchemyError

from common.logging import configure_logging, get_logger
from feature_log.events import EventError, parse_event
from feature_log.store import FeatureLogStore, Outcome

log = get_logger(__name__)
MAX_BODY = 1024 * 1024  # 1 MiB: a 1,000-wine batch is about 250 kB


def database_url(env: Mapping[str, str]) -> URL:
    raw = env.get("FEATURE_LOG_DATABASE_URL")
    if not raw:
        raise RuntimeError("FEATURE_LOG_DATABASE_URL is not set")
    url = sa.make_url(raw)
    password = env.get("FEATURE_LOG_DB_PASSWORD")
    return url.set(password=password) if password else url


def make_engine(env: Mapping[str, str]) -> Engine:
    url = database_url(env)
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if url.get_backend_name() == "postgresql":
        kwargs.update(pool_size=2, max_overflow=2, connect_args={"connect_timeout": 5})
    return sa.create_engine(url, **kwargs)


def create_app(engine: Engine | None = None, env: Mapping[str, str] | None = None) -> FastAPI:
    env = os.environ if env is None else env

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        store = FeatureLogStore(engine or make_engine(env))
        log.info("feature log database: %s", store.engine.url.render_as_string(hide_password=True))
        try:
            store.ensure_schema()
        except SQLAlchemyError:  # stay alive, not ready; readiness retries
            log.exception("database not available at startup")
        app.state.store = store
        yield
        store.engine.dispose()

    app = FastAPI(title="Feature logger", version="0.1.0", lifespan=lifespan)

    def store_of(request: Request) -> FeatureLogStore:
        return request.app.state.store

    @app.post("/", status_code=status.HTTP_202_ACCEPTED)
    async def receive(request: Request) -> JSONResponse:
        store = store_of(request)
        body = await request.body()
        if len(body) > MAX_BODY:
            store.count("rejected")
            return JSONResponse({"error": "body too large"}, status_code=413)
        try:
            event = parse_event(request.headers, body)
        except EventError as exc:
            store.count("rejected")
            log.warning("rejected event: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)
        try:
            if not store.schema_ready:
                await run_in_threadpool(store.ensure_schema)
            outcome = await run_in_threadpool(store.handle, event)
        except SQLAlchemyError:
            store.count("error")
            log.exception("could not store event %s (%s)", event.event_id, event.kind)
            return JSONResponse(
                {"error": "database unavailable"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        if outcome is not Outcome.STORED:
            log.debug("dropped %s event %s: %s", event.kind, event.event_id, outcome.value)
        return JSONResponse({"outcome": outcome.value}, status_code=status.HTTP_202_ACCEPTED)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(request: Request) -> JSONResponse:
        store = store_of(request)
        try:
            store.ping()
            store.ensure_schema()
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request) -> str:
        counts = store_of(request).counts
        lines = [
            "# HELP feature_log_events_total CloudEvents received, by outcome.",
            "# TYPE feature_log_events_total counter",
        ]
        outcomes = [o.value for o in Outcome] + ["rejected", "error"]
        lines += [f'feature_log_events_total{{outcome="{o}"}} {counts[o]}' for o in outcomes]
        return "\n".join(lines) + "\n"

    return app
