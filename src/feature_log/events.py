"""CloudEvents sent by KServe's logger sidecar (binary content mode).

Every call to the predictor produces up to two HTTP ``POST /`` deliveries that share
one ``Ce-Id``: the request (``Ce-Type: org.kubeflow.serving.inference.request``) and,
for 2xx answers only, the response (``…inference.response``). The body is the raw
payload. Format checked against KServe v0.21.0 in task 1.2
(``tests/feature_log/fixtures/``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

TYPE_PREFIX = "org.kubeflow.serving.inference."
Kind = Literal["request", "response"]


class EventError(ValueError):
    """Not an inference CloudEvent (missing id, unknown type)."""


@dataclass(frozen=True)
class InferenceEvent:
    event_id: str
    kind: Kind
    inference_service: str | None
    namespace: str | None
    component: str | None
    time: datetime
    body: str


def _time(value: str | None) -> datetime:
    """``Ce-Time`` (RFC 3339, nanoseconds) as an aware UTC datetime; now if absent/bad."""
    if value:
        # Python parses at most 6 fractional digits; KServe sends 9.
        trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value.strip())
        try:
            parsed = datetime.fromisoformat(trimmed)
        except ValueError:
            parsed = None
        if parsed is not None:
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(
                timezone.utc
            )
    return datetime.now(timezone.utc)


def parse_event(headers: Mapping[str, str], body: bytes) -> InferenceEvent:
    """Read one binary-mode CloudEvent; raise :class:`EventError` if it is not one."""
    ce = {k.lower(): v for k, v in headers.items() if k.lower().startswith("ce-")}
    event_id = ce.get("ce-id", "").strip()
    if not event_id:
        raise EventError("missing ce-id header")
    event_type = ce.get("ce-type", "")
    kind = event_type.removeprefix(TYPE_PREFIX)
    if not event_type.startswith(TYPE_PREFIX) or kind not in ("request", "response"):
        raise EventError(f"unsupported ce-type {event_type!r}")
    return InferenceEvent(
        event_id=event_id,
        kind=kind,  # type: ignore[arg-type]
        inference_service=ce.get("ce-inferenceservicename") or None,
        namespace=ce.get("ce-namespace") or None,
        component=ce.get("ce-component") or None,
        time=_time(ce.get("ce-time")),
        body=body.decode("utf-8", errors="replace"),
    )
