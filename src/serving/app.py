"""FastAPI inference service.

Endpoints:

* ``POST /predict``: JSON prediction (one wine, or ``{"instances": [...]}``)
* ``GET /health/live`` and ``GET /health/ready``: Kubernetes probes
* ``GET /`` and ``POST /``: browser form using the same validation as ``/predict``
* ``/v2/...``: Open Inference Protocol v2 (see :mod:`serving.v2`), same validation

The model is loaded once, at startup, from ``MODEL_URI``. There is deliberately no
endpoint that trains a model or runs a command.

Run locally with ``uvicorn serving.app:app``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from common.config import load_settings
from common.logging import configure_logging, get_logger
from data.features import FEATURE_COLUMNS
from serving.model import ModelStore, ModelUnavailableError
from serving.schemas import (
    BatchRequest,
    BatchResponse,
    HealthResponse,
    PredictResponse,
    WineFeatures,
)
from serving.v2 import build_router as build_v2_router

log = get_logger(__name__)
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")


#: Seconds between load retries while no model is loaded (readiness probe driven).
RELOAD_INTERVAL = float(os.environ.get("MODEL_RELOAD_INTERVAL", "30"))


def _resolve_model_uri() -> str:
    return os.environ.get("MODEL_URI") or load_settings().model_uri


def _parse(payload: Any) -> tuple[list[WineFeatures], bool]:
    """Validate a single object or an ``instances`` batch; 422 on any problem."""
    try:
        if isinstance(payload, dict) and "instances" in payload:
            return BatchRequest.model_validate(payload).instances, True
        return [WineFeatures.model_validate(payload)], False
    except ValidationError as exc:
        errors = [
            {**err, "loc": ("body", *err["loc"])}
            for err in exc.errors(include_url=False, include_context=False)
        ]
        raise RequestValidationError(errors) from exc


def _form_payload(form: dict[str, Any]) -> dict[str, Any]:
    """Browser forms send text: convert numeric strings so the JSON rules apply."""
    payload: dict[str, Any] = {}
    for key, value in form.items():
        try:
            payload[key] = float(value)
        except (TypeError, ValueError):
            payload[key] = value
    return payload


def create_app(model_uri: str | None = None, model_name: str | None = None) -> FastAPI:
    """``model_name`` is the name served under ``/v2/models/`` (default: the registry's)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        store = ModelStore(model_uri or _resolve_model_uri())
        try:
            store.load()
        except Exception:  # noqa: BLE001 - a broken model must not kill liveness
            log.exception("model failed to load from %s", store.uri)
        app.state.store = store
        app.state.reload_interval = RELOAD_INTERVAL
        yield

    app = FastAPI(
        title="Wine quality classifier",
        version="0.2.0",
        summary="Predicts whether a red wine is good (quality >= 6).",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(
        build_v2_router(model_name or load_settings().tracking.model_name, app.version)
    )

    def store_of(request: Request) -> ModelStore:
        return request.app.state.store

    def predict_rows(store: ModelStore, rows: list[WineFeatures]) -> list[PredictResponse]:
        try:
            results = store.predict([r.as_row() for r in rows])
        except ModelUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "model not loaded") from exc
        return [
            PredictResponse(
                prediction=r.prediction,
                label=r.label,
                probability=r.probability,
                model_version=store.version or "unknown",
            )
            for r in results
        ]

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    def ready(request: Request) -> JSONResponse:
        store = store_of(request)
        store.ensure_loaded(RELOAD_INTERVAL)
        body = HealthResponse(
            status="ok" if store.ready else "unavailable", model_version=store.version
        )
        code = status.HTTP_200_OK if store.ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(body.model_dump(), status_code=code)

    @app.post(
        "/predict",
        response_model=PredictResponse | BatchResponse,
        tags=["predict"],
        responses={422: {"description": "Invalid features"}, 503: {"description": "No model"}},
    )
    def predict(
        request: Request,
        payload: Any = Body(
            ...,
            description="One WineFeatures object, or {'instances': [WineFeatures, ...]}",
            examples=[WineFeatures.model_config["json_schema_extra"]["example"]],
        ),
    ) -> PredictResponse | BatchResponse:
        rows, batch = _parse(payload)
        store = store_of(request)
        predictions = predict_rows(store, rows)
        if batch:
            return BatchResponse(predictions=predictions, model_version=store.version or "")
        return predictions[0]

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "index.html", {"features": FEATURE_COLUMNS, "values": {}, "errors": {}}
        )

    @app.post("/", response_class=HTMLResponse, include_in_schema=False)
    async def submit(request: Request) -> HTMLResponse:
        form_data = dict(await request.form())
        try:
            [row], _ = _parse(_form_payload(form_data))
        except RequestValidationError as exc:
            errors = {str(e["loc"][-1]): e["msg"] for e in exc.errors()}
            return templates.TemplateResponse(
                request,
                "index.html",
                {"features": FEATURE_COLUMNS, "values": form_data, "errors": errors},
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        [result] = predict_rows(store_of(request), [row])
        return templates.TemplateResponse(request, "result.html", {"result": result})

    return app


app = create_app()
