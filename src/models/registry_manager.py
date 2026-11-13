"""Champion promotion and rollback in the MLflow model registry.

Usage::

    python -m models.registry_manager evaluate --version N [--dry-run]
    python -m models.registry_manager rollback

``evaluate`` decides whether registered version ``N`` becomes the champion:

1. **Absolute gate.** Its ROC-AUC and F1 on its own held-out test set
   (``data/test.csv`` of its run) must meet ``min_roc_auc`` and ``min_f1``.
2. **Champion comparison.** If a champion exists, both models are re-scored on that
   *same* test set; the candidate must beat the champion's ROC-AUC by at least
   ``min_improvement``. Logged metrics are not trusted for this comparison, because
   the champion may have been evaluated on a different split.
3. **Promotion.** ``previous-champion`` moves to the old champion *first*, then
   ``champion`` moves to the candidate (registry aliases; MLflow stages are deprecated).

Every decision is recorded as tags on the candidate version and printed as one JSON
object on stdout. Exit status: 0 whenever a decision is reached (promoted or not),
1 on errors (unknown version, missing test set, unreachable registry, no previous
champion to roll back to), so an orchestrator can tell failures from rejections.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from common.config import PromotionParams, load_settings
from common.logging import configure_logging, get_logger
from training.evaluate import evaluate
from training.tracking import TEST_ARTIFACT, resolve_target

log = get_logger(__name__)
TARGET = "is_good"


class RegistryError(RuntimeError):
    """The registry cannot answer the request (not a promotion rejection)."""


@dataclass
class Decision:
    model_name: str
    candidate_version: str
    promoted: bool
    reason: str
    dry_run: bool
    candidate: dict[str, float]
    champion_version: str | None = None
    champion: dict[str, float] | None = None
    thresholds: dict[str, float] = field(default_factory=dict)
    decided_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RegistryManager:
    def __init__(
        self,
        client: Any,
        model_name: str,
        promotion: PromotionParams,
        champion_alias: str = "champion",
        previous_alias: str = "previous-champion",
        registry_uri: str | None = None,
    ) -> None:
        self.client = client
        # MLflow's model loading goes through process-global URIs (which MLflow also
        # exports as environment variables). Pin them to *this* registry before every
        # load, or a model with the same name/version from another registry could be
        # scored.
        self.registry_uri = registry_uri or client._registry_uri
        self.model_name = model_name
        self.promotion = promotion
        self.champion_alias = champion_alias
        self.previous_alias = previous_alias

    # ---------------------------------------------------------------- queries
    def _by_alias(self, alias: str) -> Any | None:
        from mlflow.exceptions import MlflowException

        try:
            return self.client.get_model_version_by_alias(self.model_name, alias)
        except MlflowException as exc:
            if exc.error_code in ("RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"):
                return None
            raise

    def champion(self) -> Any | None:
        return self._by_alias(self.champion_alias)

    def _version(self, version: str) -> Any:
        from mlflow.exceptions import MlflowException

        try:
            return self.client.get_model_version(self.model_name, str(version))
        except MlflowException as exc:
            raise RegistryError(
                f"unknown model version {self.model_name}/{version}: {exc.message}"
            ) from exc

    def _test_set(self, run_id: str) -> pd.DataFrame:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = self.client.download_artifacts(run_id, TEST_ARTIFACT, tmp)
                return pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001 - any failure means "no test set"
            raise RegistryError(f"test set {TEST_ARTIFACT} missing for run {run_id}") from exc

    def _score(self, version: str, test: pd.DataFrame) -> dict[str, float]:
        import mlflow
        import mlflow.sklearn

        mlflow.set_tracking_uri(self.registry_uri)
        mlflow.set_registry_uri(self.registry_uri)
        model = mlflow.sklearn.load_model(f"models:/{self.model_name}/{version}")
        metrics = evaluate(model, test, TARGET)
        return {"roc_auc": float(metrics["roc_auc"]), "f1": float(metrics["f1"])}

    # ---------------------------------------------------------------- commands
    def evaluate(self, version: str, dry_run: bool = False) -> Decision:
        candidate = self._version(version)
        test = self._test_set(candidate.run_id)
        scores = self._score(candidate.version, test)
        p = self.promotion
        decision = Decision(
            model_name=self.model_name,
            candidate_version=str(candidate.version),
            promoted=False,
            reason="",
            dry_run=dry_run,
            candidate=scores,
            thresholds={
                "min_roc_auc": p.min_roc_auc,
                "min_f1": p.min_f1,
                "min_improvement": p.min_improvement,
            },
            decided_at=_now(),
        )

        failed = []
        if scores["roc_auc"] < p.min_roc_auc:
            failed.append(f"roc_auc {scores['roc_auc']:.4f} < min_roc_auc {p.min_roc_auc}")
        if scores["f1"] < p.min_f1:
            failed.append(f"f1 {scores['f1']:.4f} < min_f1 {p.min_f1}")

        champion = self.champion()
        if champion is not None:
            decision.champion_version = str(champion.version)

        if failed:
            decision.reason = "below threshold: " + "; ".join(failed)
        elif champion is None:
            decision.promoted = True
            decision.reason = "first champion: no version holds the champion alias"
        elif str(champion.version) == str(candidate.version):
            decision.reason = "candidate is already the champion"
        else:
            decision.champion = self._score(champion.version, test)
            needed = decision.champion["roc_auc"] + p.min_improvement
            if scores["roc_auc"] >= needed:
                decision.promoted = True
                decision.reason = (
                    f"roc_auc {scores['roc_auc']:.4f} beats champion v{champion.version} "
                    f"{decision.champion['roc_auc']:.4f} by >= {p.min_improvement}"
                )
            else:
                decision.reason = (
                    f"did not beat champion v{champion.version}: roc_auc "
                    f"{scores['roc_auc']:.4f} < {decision.champion['roc_auc']:.4f} "
                    f"+ {p.min_improvement}"
                )

        if not dry_run:
            self._apply(decision, champion)
        log.info("%s: %s", "dry run" if dry_run else "decision", decision.reason)
        return decision

    def _apply(self, decision: Decision, champion: Any | None) -> None:
        name, version = self.model_name, decision.candidate_version
        if decision.promoted:
            # previous-champion first: a crash in between leaves the old champion serving.
            if champion is not None:
                self.client.set_registered_model_alias(name, self.previous_alias, champion.version)
            self.client.set_registered_model_alias(name, self.champion_alias, version)
        tags = {
            "promotion_status": "promoted" if decision.promoted else "rejected",
            "promotion_reason": decision.reason,
            "promotion_decided_at": decision.decided_at,
            "candidate_roc_auc": f"{decision.candidate['roc_auc']:.6f}",
            "candidate_f1": f"{decision.candidate['f1']:.6f}",
        }
        if decision.champion is not None:
            tags["champion_version"] = str(decision.champion_version)
            tags["champion_roc_auc"] = f"{decision.champion['roc_auc']:.6f}"
        for key, value in tags.items():
            self.client.set_model_version_tag(name, version, key, value)

    def rollback(self) -> dict[str, str]:
        previous = self._by_alias(self.previous_alias)
        if previous is None:
            raise RegistryError(
                f"no '{self.previous_alias}' alias on {self.model_name}: nothing to roll back to"
            )
        current = self.champion()
        name, now = self.model_name, _now()
        self.client.set_registered_model_alias(name, self.champion_alias, previous.version)
        self.client.delete_registered_model_alias(name, self.previous_alias)
        self.client.set_model_version_tag(name, previous.version, "restored_at", now)
        if current is not None and str(current.version) != str(previous.version):
            self.client.set_model_version_tag(name, current.version, "rolled_back_at", now)
            self.client.set_model_version_tag(
                name, current.version, "promotion_status", "rolled_back"
            )
        result = {
            "model_name": name,
            "champion_version": str(previous.version),
            "rolled_back_version": None if current is None else str(current.version),
            "rolled_back_at": now,
        }
        log.info("rolled back %s to version %s", name, previous.version)
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m models.registry_manager")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate_cmd = commands.add_parser("evaluate", help="decide whether a version becomes champion")
    evaluate_cmd.add_argument("--version", required=True, help="registered model version")
    evaluate_cmd.add_argument("--dry-run", action="store_true", help="decide, change nothing")
    commands.add_parser("rollback", help="make previous-champion the champion again")
    return parser


def build_manager(env: Mapping[str, str] | None = None) -> RegistryManager:
    from mlflow import MlflowClient

    env = os.environ if env is None else env
    settings = load_settings(env=env)
    target = resolve_target(env, settings.output_dir)
    client = MlflowClient(tracking_uri=target.uri, registry_uri=target.uri)
    return RegistryManager(
        client,
        model_name=settings.tracking.model_name,
        promotion=settings.params.promotion,
        champion_alias=settings.tracking.champion_alias,
        previous_alias=settings.tracking.previous_alias,
        registry_uri=target.uri,
    )


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    configure_logging()
    args = _parser().parse_args(argv)
    try:
        manager = build_manager(env)
        if args.command == "evaluate":
            print(manager.evaluate(args.version, dry_run=args.dry_run).to_json())
        else:
            print(json.dumps(manager.rollback(), sort_keys=True))
    except RegistryError as exc:
        log.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001 - unreachable registry, bad config, ...
        log.exception("registry command failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
