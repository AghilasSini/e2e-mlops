"""Feature-log storage: SQLAlchemy Core on PostgreSQL (production) or SQLite (tests).

Two tables:

``inference_log``
    one row per call, keyed by the CloudEvent id shared by the request and the response:
    raw request and response bodies, model version, InferenceService, time received and
    ``status`` (``parsed`` or ``unparsed``).
``inference_features``
    one row per predicted wine, keyed by (``event_id``, ``row_index``): the 11 features,
    ``prediction``, ``probability`` (of *good*), ``model_version``, ``received_at``.
    Drift monitoring (Epic 5) reads this table against the training reference set.

Every event is an upsert, so the request and the response can arrive in either order
and a redelivered event changes nothing. Each event is handled in one transaction; its
first statement locks the ``inference_log`` row, so the two halves of a call are
applied one after the other even when they arrive together.
"""

from __future__ import annotations

import enum
import threading
from collections import Counter
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

from data.columns import FEATURE_COLUMNS
from feature_log.events import InferenceEvent
from feature_log.parsing import ParsedRequest, ParsedResponse, parse_request, parse_response

metadata = sa.MetaData()

inference_log = sa.Table(
    "inference_log",
    metadata,
    sa.Column("event_id", sa.String(128), primary_key=True),
    sa.Column("inference_service", sa.String(253)),
    sa.Column("namespace", sa.String(63)),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("model_version", sa.String(255)),
    # Raw payloads as received (not necessarily JSON: rejected requests are kept as-is).
    sa.Column("request_body", sa.Text),
    sa.Column("response_body", sa.Text),
    sa.Column("status", sa.String(16), nullable=False),
)

inference_features = sa.Table(
    "inference_features",
    metadata,
    sa.Column(
        "event_id",
        sa.String(128),
        sa.ForeignKey("inference_log.event_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("row_index", sa.Integer, primary_key=True),
    *(sa.Column(name, sa.Float) for name in FEATURE_COLUMNS),
    sa.Column("prediction", sa.SmallInteger),
    sa.Column("probability", sa.Float),
    sa.Column("model_version", sa.String(255)),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, index=True),
)


class Outcome(str, enum.Enum):
    STORED = "stored"
    DROPPED_EMPTY = "dropped_empty"  # GET calls through the Service (health checks, ...)
    DROPPED_NOT_PREDICTION = "dropped_not_prediction"  # their answer, arriving first


def _insert(conn: Connection, table: sa.Table) -> Any:
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif conn.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - only PostgreSQL and SQLite are supported
        raise RuntimeError(f"unsupported database {conn.dialect.name}")
    return insert(table)


def _upsert(
    conn: Connection,
    table: sa.Table,
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    keep: tuple[str, ...] = (),
) -> None:
    """Insert ``rows``; on conflict update their columns, except ``keep`` (set on insert)."""
    if not rows:
        return
    stmt = _insert(conn, table).values(rows)
    updates = {c: stmt.excluded[c] for c in rows[0] if c not in keys and c not in keep}
    if updates:
        stmt = stmt.on_conflict_do_update(index_elements=list(keys), set_=updates)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(keys))
    conn.execute(stmt)


def _status(request: str | None, response: str | None) -> str:
    parsed_request = parse_request(request) if request is not None else None
    parsed_response = parse_response(response) if response is not None else None
    if request is not None and parsed_request is None:
        return "unparsed"
    if response is not None and parsed_response is None:
        return "unparsed"
    if (
        parsed_request is not None
        and parsed_response is not None
        and len(parsed_request.rows) != len(parsed_response.predictions)
    ):
        return "unparsed"
    return "parsed"


class FeatureLogStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.counts: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._schema_lock = threading.Lock()
        self.schema_ready = False

    def ensure_schema(self) -> None:
        """Create the tables once. Safe when called by concurrent requests (the database
        often appears only after the service started, then two events arrive together)
        and when another process creates them at the same time."""
        if self.schema_ready:
            return
        with self._schema_lock:
            if self.schema_ready:
                return
            try:
                metadata.create_all(self.engine)
            except (IntegrityError, ProgrammingError):
                # Created concurrently elsewhere: the second pass sees the tables.
                metadata.create_all(self.engine)
            self.schema_ready = True

    def ping(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))

    def count(self, outcome: str) -> None:
        with self._lock:
            self.counts[outcome] += 1

    # ---------------------------------------------------------------- events
    def handle(self, event: InferenceEvent) -> Outcome:
        if event.kind == "request":
            outcome = self._request(event)
        else:
            outcome = self._response(event)
        self.count(outcome.value)
        return outcome

    def _request(self, event: InferenceEvent) -> Outcome:
        if not event.body.strip():
            return Outcome.DROPPED_EMPTY
        parsed = parse_request(event.body)
        with self.engine.begin() as conn:
            _upsert(
                conn,
                inference_log,
                [
                    {
                        "event_id": event.event_id,
                        "inference_service": event.inference_service,
                        "namespace": event.namespace,
                        "received_at": event.time,  # the request's time wins
                        "request_body": event.body,
                        "status": "unparsed",  # recomputed below
                    }
                ],
                keys=("event_id",),
                keep=("status",),
            )
            if parsed is not None:
                self._feature_rows(conn, event.event_id, event.time, request=parsed)
            self._refresh(conn, event.event_id)
        return Outcome.STORED

    def _response(self, event: InferenceEvent) -> Outcome:
        parsed = parse_response(event.body)
        with self.engine.begin() as conn:
            if parsed is None:
                exists = conn.execute(
                    sa.select(inference_log.c.event_id)
                    .where(inference_log.c.event_id == event.event_id)
                    .with_for_update()
                ).first()
                if exists is None:
                    return Outcome.DROPPED_NOT_PREDICTION
            row: dict[str, Any] = {
                "event_id": event.event_id,
                "inference_service": event.inference_service,
                "namespace": event.namespace,
                "received_at": event.time,  # kept if the request is already stored
                "response_body": event.body,
                "status": "unparsed",
            }
            if parsed is not None:
                row["model_version"] = parsed.model_version
            _upsert(
                conn,
                inference_log,
                [row],
                keys=("event_id",),
                keep=("status", "received_at", "inference_service", "namespace"),
            )
            if parsed is not None:
                self._feature_rows(conn, event.event_id, event.time, response=parsed)
            self._refresh(conn, event.event_id)
        return Outcome.STORED

    def _feature_rows(
        self,
        conn: Connection,
        event_id: str,
        time: datetime,
        request: ParsedRequest | None = None,
        response: ParsedResponse | None = None,
    ) -> None:
        if request is not None:
            rows = [
                {"event_id": event_id, "row_index": i, **features, "received_at": time}
                for i, features in enumerate(request.rows)
            ]
            _upsert(conn, inference_features, rows, keys=("event_id", "row_index"))
        if response is not None:
            rows = [
                {
                    "event_id": event_id,
                    "row_index": i,
                    "prediction": p.prediction,
                    "probability": p.probability,
                    "model_version": response.model_version,
                    "received_at": time,
                }
                for i, p in enumerate(response.predictions)
            ]
            _upsert(
                conn,
                inference_features,
                rows,
                keys=("event_id", "row_index"),
                keep=("received_at",),
            )

    def _refresh(self, conn: Connection, event_id: str) -> None:
        """Recompute the entry's status; an unparsed entry keeps no feature rows."""
        log = conn.execute(
            sa.select(
                inference_log.c.request_body,
                inference_log.c.response_body,
                inference_log.c.received_at,
                inference_log.c.model_version,
            ).where(inference_log.c.event_id == event_id)
        ).one()
        status = _status(log.request_body, log.response_body)
        conn.execute(
            inference_log.update().where(inference_log.c.event_id == event_id).values(status=status)
        )
        features = inference_features.c
        if status == "unparsed":
            conn.execute(inference_features.delete().where(features.event_id == event_id))
            return
        # Rows written by the response before the request keep the request's time.
        conn.execute(
            inference_features.update()
            .where(features.event_id == event_id)
            .values(received_at=log.received_at)
        )
        if log.model_version is not None:
            conn.execute(
                inference_features.update()
                .where(features.event_id == event_id)
                .values(model_version=log.model_version)
            )
