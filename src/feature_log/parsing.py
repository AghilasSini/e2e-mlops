"""Turn logged payloads into feature rows, predictions and the model version.

The logger sidecar does not say which path was called, so payloads are recognised by
shape:

==========================  =============================  =====================================
format                      request                        response
==========================  =============================  =====================================
JSON API, one wine          ``{<11 features>}``            ``{"prediction", "probability", …}``
JSON API, batch             ``{"instances": [{…}, …]}``    ``{"predictions": [{…}, …], …}``
Open Inference Protocol v2  ``{"inputs": [{name, data}]}``  ``{"outputs": [{name, data}], …}``
==========================  =============================  =====================================

Anything else (a rejected request such as ``{"alcohol": "abc"}``, the HTML form, a
health check answer) is *unparsed*: the functions return ``None`` and the store keeps
the raw payload. The predictor already validated every request it answered, so the
logger only checks the shape: the 11 known features, all numbers.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from data.columns import FEATURE_COLUMNS

FEATURES = frozenset(FEATURE_COLUMNS)


@dataclass(frozen=True)
class ParsedRequest:
    rows: list[dict[str, float]]  # one dict of the 11 features per wine, in request order


@dataclass(frozen=True)
class Prediction:
    prediction: int | None
    probability: float | None


@dataclass(frozen=True)
class ParsedResponse:
    predictions: list[Prediction]  # in request order
    model_version: str | None


def _json(body: str) -> Any:
    try:
        return json.loads(body) if body.strip() else None
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _row(item: Any) -> dict[str, float] | None:
    if not isinstance(item, dict) or set(item) != FEATURES:
        return None
    row = {name: _number(item[name]) for name in FEATURE_COLUMNS}
    return row if all(v is not None for v in row.values()) else None  # type: ignore[return-value]


def _flatten(data: Any) -> list[Any] | None:
    if not isinstance(data, list):
        return None
    flat: list[Any] = []
    for item in data:
        if isinstance(item, list):
            inner = _flatten(item)
            if inner is None:
                return None
            flat.extend(inner)
        else:
            flat.append(item)
    return flat


def _oip_columns(items: Any) -> dict[str, list[Any]] | None:
    if not isinstance(items, list):
        return None
    columns: dict[str, list[Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return None
        values = _flatten(item.get("data"))
        if values is None or item["name"] in columns:
            return None
        columns[item["name"]] = values
    return columns


def parse_request(body: str) -> ParsedRequest | None:
    payload = _json(body)
    if not isinstance(payload, dict):
        return None
    if "inputs" in payload:  # OIP v2
        columns = _oip_columns(payload["inputs"])
        if columns is None or set(columns) != FEATURES:
            return None
        lengths = {len(v) for v in columns.values()}
        if len(lengths) != 1 or 0 in lengths:
            return None
        n = lengths.pop()
        rows = [_row({name: columns[name][i] for name in FEATURE_COLUMNS}) for i in range(n)]
    elif "instances" in payload:  # JSON API batch
        instances = payload["instances"]
        if set(payload) != {"instances"} or not isinstance(instances, list) or not instances:
            return None
        rows = [_row(item) for item in instances]
    else:  # JSON API, one wine
        rows = [_row(payload)]
    if any(r is None for r in rows):
        return None
    return ParsedRequest(rows=rows)  # type: ignore[arg-type]


def _prediction(item: Any) -> Prediction | None:
    if not isinstance(item, dict):
        return None
    prediction, probability = item.get("prediction"), _number(item.get("probability"))
    if prediction not in (0, 1) or isinstance(prediction, bool) or probability is None:
        return None
    return Prediction(prediction=int(prediction), probability=probability)


def _version(payload: dict[str, Any]) -> str | None:
    version = payload.get("model_version")
    return version if isinstance(version, str) and version else None


def parse_response(body: str) -> ParsedResponse | None:
    payload = _json(body)
    if not isinstance(payload, dict):
        return None
    if "outputs" in payload:  # OIP v2: either output may be missing (requested outputs)
        columns = _oip_columns(payload["outputs"])
        if columns is None or not ({"prediction", "probability"} & set(columns)):
            return None
        lengths = {len(columns[name]) for name in ("prediction", "probability") if name in columns}
        if len(lengths) != 1:
            return None
        n = lengths.pop()
        labels = columns.get("prediction", [None] * n)
        scores = columns.get("probability", [None] * n)
        if any(p not in (0, 1, None) or isinstance(p, bool) for p in labels):
            return None
        if any(s is not None and _number(s) is None for s in scores):
            return None
        predictions = [
            Prediction(
                prediction=None if p is None else int(p),
                probability=None if s is None else float(s),
            )
            for p, s in zip(labels, scores)
        ]
    elif "predictions" in payload:  # JSON API batch
        items = payload["predictions"]
        parsed = [_prediction(item) for item in items] if isinstance(items, list) else [None]
        if not parsed or any(p is None for p in parsed):
            return None
        predictions = parsed  # type: ignore[assignment]
    else:  # JSON API, one wine
        single = _prediction(payload)
        if single is None:
            return None
        predictions = [single]
    return ParsedResponse(predictions=predictions, model_version=_version(payload))
