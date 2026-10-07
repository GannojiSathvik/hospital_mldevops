"""Retraining pipeline (Prefect flow) with access control and champion/challenger.

Steps:
  1. RBAC        - caller's PIPELINE_API_KEY must have `pipeline:retrain`
                   (skipped only if ENFORCE_PIPELINE_RBAC=false).
  2. Decision    - read reports/drift/retrain_decision.json (written by the
                   monitoring flow); stop if retraining is not needed, unless --force.
  3. Data        - training data = data/processed/train.csv + labelled production
                   feedback (data/inference/labeled_feedback.csv) if present.
  4. Train       - the normal training code (src.models.train.run_training) trains
                   a CHALLENGER into a staging folder; production files are untouched.
  5. Compare     - champion (current production model) and challenger are both
                   scored on the SAME held-out test set (data/processed/test.csv).
                   The challenger is promoted only if its PR-AUC is not worse.
  6. Promote     - old artifacts are backed up to models/archive/<timestamp>/,
                   then the challenger's artifacts are copied into models/.
  7. Record      - outcome written to reports/retraining/decision.json.

Run:
  python -m pipelines.retraining_pipeline            # obey the monitoring decision
  python -m pipelines.retraining_pipeline --force    # retrain regardless
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from pipelines.monitoring_pipeline import flow, task  # real Prefect or passthrough (USE_PREFECT)
from src.data.preprocess import read_patient_csv
from src.features.schema import FEATURE_COLUMNS, ID_COLUMN, TARGET

MODELS_DIR = Path("models")
TRAIN_PATH = Path("data/processed/train.csv")
TEST_PATH = Path("data/processed/test.csv")
FEEDBACK_PATH = Path("data/inference/labeled_feedback.csv")
MONITOR_DECISION_PATH = Path("reports/drift/retrain_decision.json")
OUT_DIR = Path("reports/retraining")
# Files that together make up one "model version" (names fixed by CONTRACT.md).
ARTIFACTS = [
    "readmission_model.joblib",
    "preprocessor.joblib",
    "threshold.json",
    "model_metadata.json",
    "metrics.json",
    "feature_list.json",
]


@task(name="rbac-check")
def check_access() -> str:
    """Enforce RBAC unless explicitly disabled; returns who is running the job."""
    if os.getenv("ENFORCE_PIPELINE_RBAC", "true").lower() == "false":
        return "local-dev (RBAC disabled)"
    from src.security.rbac import require_pipeline_permission  # lazy import

    return require_pipeline_permission("pipeline:retrain").name


@task(name="read-monitoring-decision")
def read_decision() -> dict | None:
    if not MONITOR_DECISION_PATH.exists():
        return None
    return json.loads(MONITOR_DECISION_PATH.read_text())


@task(name="build-training-data")
def build_training_data(staging: Path) -> tuple[str, int]:
    """Append labelled production feedback to the original training split.
    The test split is NOT changed, so champion and challenger stay comparable."""
    train = read_patient_csv(TRAIN_PATH)
    n_feedback = 0
    if FEEDBACK_PATH.exists():
        feedback = read_patient_csv(FEEDBACK_PATH)
        feedback = feedback[FEATURE_COLUMNS + [TARGET]].copy()
        n_feedback = len(feedback)
        # Feedback rows have no patient_id in the log; give them clearly-marked IDs.
        feedback.insert(0, ID_COLUMN, [f"FB{i:06d}" for i in range(1, n_feedback + 1)])
        train = pd.concat([train, feedback[train.columns]], ignore_index=True)
    path = staging / "train_merged.csv"
    train.to_csv(path, index=False)
    return str(path), n_feedback


@task(name="train-challenger")
def train_challenger(train_path: str, staging: Path, trained_by: str) -> None:
    """Re-use the project's training code, but write everything into `staging`."""
    from src.models.train import run_training

    run_training(
        train_path=train_path,
        test_path=str(TEST_PATH),
        models_dir=str(staging / "models"),
        figures_dir=str(staging / "figures"),
        evaluation_dir=str(staging / "evaluation"),
        trained_by=trained_by,
    )


def _score(model_path: Path, test: pd.DataFrame) -> dict:
    model = joblib.load(model_path)
    probs = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
    y = test[TARGET].astype(int)
    return {
        "pr_auc": round(float(average_precision_score(y, probs)), 4),
        "roc_auc": round(float(roc_auc_score(y, probs)), 4),
    }


@task(name="champion-vs-challenger")
def compare(staging: Path) -> dict:
    """Score both models on the same held-out test set; PR-AUC decides."""
    test = read_patient_csv(TEST_PATH)
    challenger = _score(staging / "models" / "readmission_model.joblib", test)
    champion_path = MODELS_DIR / "readmission_model.joblib"
    champion = _score(champion_path, test) if champion_path.exists() else None
    recorded = None
    if (MODELS_DIR / "metrics.json").exists():  # what training recorded for the champion
        recorded = (
            json.loads((MODELS_DIR / "metrics.json").read_text())
            .get("best_model_test_metrics", {})
            .get("pr_auc")
        )
    promote = champion is None or challenger["pr_auc"] >= champion["pr_auc"]
    return {
        "champion": champion,
        "challenger": challenger,
        "champion_recorded_pr_auc": recorded,
        "promote": promote,
    }


@task(name="promote-challenger")
def promote(staging: Path, stamp: str, merged_train_path: str, n_feedback: int) -> str:
    """Back up current production artifacts, then install the challenger."""
    archive = MODELS_DIR / "archive" / stamp
    archive.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        if (MODELS_DIR / name).exists():
            shutil.copy2(MODELS_DIR / name, archive / name)
    shutil.copy2(TRAIN_PATH, archive / "train.csv")  # reference data the old model used
    for name in ARTIFACTS:
        src = staging / "models" / name
        if src.exists():
            shutil.copy2(src, MODELS_DIR / name)
    # If feedback was added, the new champion's training data becomes the new
    # drift reference (the old one is in the archive).
    if n_feedback > 0:
        shutil.copy2(merged_train_path, TRAIN_PATH)
    return str(archive)


@flow(name="retraining-flow")
def retraining_flow(force: bool = False) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result: dict = {"run_at": datetime.now(timezone.utc).isoformat(), "forced": force}

    result["triggered_by"] = check_access()
    monitor = read_decision()
    result["monitoring_decision"] = (
        {
            "retrain": monitor["retrain"],
            "reasons": monitor["reasons"],
            "evaluated_at": monitor["evaluated_at"],
        }
        if monitor
        else None
    )
    if not force and not (monitor and monitor.get("retrain")):
        result.update(
            action="skipped",
            reason="monitoring did not request retraining (use --force to override)",
        )
    else:
        staging = OUT_DIR / f"challenger_{stamp}"
        staging.mkdir(parents=True, exist_ok=True)
        train_path, n_feedback = build_training_data(staging)
        result["n_feedback_rows_added"] = n_feedback
        train_challenger(train_path, staging, result["triggered_by"])
        comparison = compare(staging)
        result["comparison"] = comparison
        if comparison["promote"]:
            result["archived_to"] = promote(staging, stamp, train_path, n_feedback)
            result.update(
                action="promoted",
                reason="challenger PR-AUC >= champion PR-AUC on the same test set",
            )
        else:
            result.update(
                action="kept_champion",
                reason="challenger PR-AUC < champion PR-AUC; production model unchanged",
            )
        result["challenger_dir"] = str(staging)

    # decision.json = latest outcome; decision_<timestamp>.json keeps the history.
    text = json.dumps(result, indent=2)
    (OUT_DIR / "decision.json").write_text(text, encoding="utf-8")
    (OUT_DIR / f"decision_{stamp}.json").write_text(text, encoding="utf-8")
    print(f"[retraining] action={result['action']}: {result['reason']}")
    if "comparison" in result:
        c = result["comparison"]
        print(f"[retraining] champion={c['champion']} challenger={c['challenger']}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Conditional retraining with champion/challenger")
    parser.add_argument(
        "--force", action="store_true", help="retrain even if monitoring did not ask for it"
    )
    args = parser.parse_args()
    try:
        retraining_flow(force=args.force)
    except PermissionError as exc:
        print(f"ACCESS DENIED: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
