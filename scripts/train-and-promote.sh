#!/usr/bin/env bash
# Train, register, then decide promotion: the training Job's command.
#
#   1. python -m training                      -> new registered version + run.json
#   2. python -m models.registry_manager evaluate --version <that version>
#   3. python -m models.serving_sync resolve   -> the champion to serve, as JSON
#
# Exits non-zero only when a command errors. A candidate that is *not* promoted is a
# normal outcome (exit 0); the decision is printed as JSON and tagged on the version.
# The LAST stdout line is the resolved champion ({"champion_version": "N", ...}):
# scripts/serve-champion.sh --from-job reads it from the Job's log. When no version
# is champion yet (first candidate rejected), that line is missing and the script
# still exits 0; serve-champion then reports that there is nothing to serve.
# Uses the same environment as training: MLFLOW_TRACKING_URI (+ credentials),
# APP_ROOT, OUTPUT_DIR, DATA_PATH.
set -euo pipefail

PYTHON=${PYTHON:-python}

"$PYTHON" -m training

RUN_FILE=$("$PYTHON" -c 'from common.config import load_settings; print(load_settings().output_dir / "run.json")')
VERSION=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))["model_version"])' "$RUN_FILE")
echo "==> evaluating registered version $VERSION for promotion"

"$PYTHON" -m models.registry_manager evaluate --version "$VERSION"

status=0
"$PYTHON" -m models.serving_sync resolve || status=$?
case $status in
  0) ;;
  1) echo "==> no champion yet: nothing to serve" >&2 ;;
  *) exit "$status" ;;
esac
