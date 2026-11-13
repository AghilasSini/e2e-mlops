"""Open Inference Protocol (OIP) v2 endpoints, for KServe and standard clients.

* ``GET /v2/health/live``, ``GET /v2/health/ready``: server health
* ``GET /v2``: server metadata
* ``GET /v2/models/<model>`` and ``GET /v2/models/<model>/ready``: model metadata/health
* ``POST /v2/models/<model>/infer``: predictions

An infer request carries one named ``FP64`` input per feature (the snake_case names of
:data:`data.features.FEATURE_COLUMNS`), all with the same number of values::

    {"inputs": [{"name": "alcohol", "shape": [2], "datatype": "FP64", "data": [9.4, 12.8]},
                ...]}

The response has an ``INT64`` ``prediction`` output and an ``FP64`` ``probability``
output (probability of ``good``), plus ``model_version`` (``<name>/<version>``).
Each row is validated by the same :class:`serving.schemas.WineFeatures` model as
``/predict``. Errors answer HTTP 400 with ``{"error": "..."}``, as the protocol says.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from fastapi import APIRouter, Body, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from data.features import FEATURE_COLUMNS
from serving.model import ModelStore, ModelUnavailableError
from serving.schemas import MAX_BATCH_SIZE, WineFeatures

INPUT_DATATYPE = "FP64"
OUTPUTS = {"prediction": "INT64", "probability": "FP64"}


class InferenceError(ValueError):
    """A request the protocol answers with 400 and ``{"error": message}``."""


def _error(code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=code)


def _flatten(data: Any) -> list[Any]:
    """OIP allows row-major nested lists as well as flat ones."""
    if not isinstance(data, list):
        raise InferenceError("'data' must be a list")
    flat: list[Any] = []
    for item in data:
        flat.extend(_flatten(item) if isinstance(item, list) else [item])
    return flat


def _columns(payload: Any) -> dict[str, list[Any]]:
    """Check the protocol envelope; return the values of each feature input."""
    if not isinstance(payload, dict) or not isinstance(payload.get("inputs"), list):
        raise InferenceError("request must be a JSON object with an 'inputs' list")
    columns: dict[str, list[Any]] = {}
    for index, item in enumerate(payload["inputs"]):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise InferenceError(f"inputs[{index}] must be an object with a 'name'")
        name = item["name"]
        if name not in FEATURE_COLUMNS:
            raise InferenceError(f"unknown input '{name}'")
        if name in columns:
            raise InferenceError(f"input '{name}' is given more than once")
        if item.get("datatype") != INPUT_DATATYPE:
            raise InferenceError(
                f"input '{name}' has datatype {item.get('datatype')!r}, expected {INPUT_DATATYPE}"
            )
        values = _flatten(item.get("data"))
        shape = item.get("shape")
        if (
            not isinstance(shape, list)
            or not all(isinstance(d, int) and d >= 0 for d in shape)
            or math.prod(shape) != len(values)
        ):
            raise InferenceError(f"input '{name}' has shape {shape!r} but {len(values)} values")
        columns[name] = values
    missing = [name for name in FEATURE_COLUMNS if name not in columns]
    if missing:
        raise InferenceError(f"missing inputs: {', '.join(missing)}")
    lengths = {name: len(values) for name, values in columns.items()}
    if len(set(lengths.values())) != 1:
        detail = ", ".join(f"{name}={n}" for name, n in lengths.items())
        raise InferenceError(f"inputs must have the same number of values ({detail})")
    rows = next(iter(lengths.values()))
    if not 1 <= rows <= MAX_BATCH_SIZE:
        raise InferenceError(f"inputs must have between 1 and {MAX_BATCH_SIZE} values, got {rows}")
    return columns


def parse_infer_request(payload: Any) -> list[WineFeatures]:
    """Turn an OIP v2 infer request into validated rows, or raise :class:`InferenceError`."""
    columns = _columns(payload)
    rows = []
    for index in range(len(columns[FEATURE_COLUMNS[0]])):
        try:
            rows.append(
                WineFeatures.model_validate({name: columns[name][index] for name in columns})
            )
        except ValidationError as exc:
            problems = "; ".join(
                f"input '{err['loc'][0]}' value {index}: {err['msg']}"
                for err in exc.errors(include_url=False)
            )
            raise InferenceError(problems) from exc
    return rows


def _requested_outputs(payload: dict[str, Any]) -> list[str]:
    requested = payload.get("outputs")
    if requested is None:
        return list(OUTPUTS)
    if not isinstance(requested, list) or not all(
        isinstance(o, dict) and isinstance(o.get("name"), str) for o in requested
    ):
        raise InferenceError("'outputs' must be a list of objects with a 'name'")
    names = [o["name"] for o in requested]
    unknown = [n for n in names if n not in OUTPUTS]
    if unknown:
        raise InferenceError(f"unknown outputs: {', '.join(unknown)}")
    return names


def build_router(model_name: str, server_version: str) -> APIRouter:
    router = APIRouter(prefix="/v2", tags=["open inference protocol v2"])

    def store_of(request: Request) -> ModelStore:
        return request.app.state.store

    def ready(request: Request) -> bool:
        store = store_of(request)
        store.ensure_loaded(request.app.state.reload_interval)
        return store.ready

    def unknown_model(name: str) -> JSONResponse | None:
        if name != model_name:
            return _error(status.HTTP_404_NOT_FOUND, f"model '{name}' not found")
        return None

    @router.get("")
    def server_metadata() -> dict[str, Any]:
        return {"name": "e2e-mlops-inference", "version": server_version, "extensions": []}

    @router.get("/health/live")
    def server_live() -> dict[str, bool]:
        return {"live": True}

    @router.get("/health/ready")
    def server_ready(request: Request) -> JSONResponse:
        is_ready = ready(request)
        code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse({"ready": is_ready}, status_code=code)

    @router.get("/models/{name}")
    def model_metadata(name: str, request: Request) -> JSONResponse:
        if (missing := unknown_model(name)) is not None:
            return missing
        version = store_of(request).version
        return JSONResponse(
            {
                "name": model_name,
                "versions": [version] if version else [],
                "platform": "sklearn",
                "inputs": [
                    {"name": f, "datatype": INPUT_DATATYPE, "shape": [-1]} for f in FEATURE_COLUMNS
                ],
                "outputs": [
                    {"name": n, "datatype": dt, "shape": [-1]} for n, dt in OUTPUTS.items()
                ],
            }
        )

    @router.get("/models/{name}/ready")
    def model_ready(name: str, request: Request) -> JSONResponse:
        if (missing := unknown_model(name)) is not None:
            return missing
        is_ready = ready(request)
        code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse({"name": model_name, "ready": is_ready}, status_code=code)

    @router.post(
        "/models/{name}/infer",
        responses={
            400: {"description": "Invalid inputs"},
            404: {"description": "Unknown model"},
            503: {"description": "No model"},
        },
    )
    def infer(name: str, request: Request, payload: Any = Body(...)) -> JSONResponse:
        if (missing := unknown_model(name)) is not None:
            return missing
        try:
            rows = parse_infer_request(payload)
            outputs = _requested_outputs(payload)
        except InferenceError as exc:
            return _error(status.HTTP_400_BAD_REQUEST, str(exc))
        store = store_of(request)
        try:
            results = store.predict([r.as_row() for r in rows])
        except ModelUnavailableError:
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "model not loaded")
        values = {
            "prediction": [r.prediction for r in results],
            "probability": [r.probability for r in results],
        }
        request_id = payload.get("id")
        return JSONResponse(
            {
                "model_name": model_name,
                "model_version": store.version or "unknown",
                "id": request_id if isinstance(request_id, str) else str(uuid.uuid4()),
                "outputs": [
                    {
                        "name": n,
                        "datatype": OUTPUTS[n],
                        "shape": [len(results)],
                        "data": values[n],
                    }
                    for n in outputs
                ],
            }
        )

    return router
