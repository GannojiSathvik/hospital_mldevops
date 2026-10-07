"""Monitoring pipeline (Prefect flow): detect drift and decide whether to retrain.

Steps (each one a Prefect *task* = a tracked, retryable unit of work):
  1. load reference data  - data/processed/train.csv (what the model learned from)
  2. load current data    - data/inference/inference_log.csv (what the API has seen)
  3. data drift report    - Evidently, saved to reports/drift/
  4. prediction shift     - change in high-risk prediction rate vs training data
  5. labelled performance - ROC-AUC on data/inference/labeled_feedback.csv (if present)
  6. retrain decision     - written to reports/drift/retrain_decision.json

Run:  python -m pipelines.monitoring_pipeline

Prefect 3 needs no server: with PREFECT_API_URL unset it starts a temporary
local API in the background for the duration of the run. If that is unwanted
(slow start, CI sandbox), set USE_PREFECT=false and the very same functions run
as plain Python.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

USE_PREFECT = os.getenv("USE_PREFECT", "true").lower() != "false"

if USE_PREFECT:
    from prefect import flow, task
else:

    def _passthrough(fn=None, **_kwargs):
        """Stand-in for @task / @flow / @task(name=...) that changes nothing."""
        if fn is None:
            return lambda f: f
        return fn

    task = flow = _passthrough

from src.data.preprocess import read_patient_csv  # noqa: E402
from src.features.schema import FEATURE_COLUMNS, TARGET  # noqa: E402
from src.monitoring.drift import (  # noqa: E402
    load_monitoring_config,
    prediction_distribution_shift,
    run_drift_report,
)
from src.monitoring.retrain_policy import should_retrain  # noqa: E402

MODEL_PATH = Path("models/readmission_model.joblib")
THRESHOLD_PATH = Path("models/threshold.json")


@task(name="load-csv")
def load_csv(path: str) -> pd.DataFrame | None:
    """Read a patient CSV (keeping the literal "None" alcohol category), or None if missing."""
    return read_patient_csv(path) if Path(path).exists() else None


def _load_model_and_threshold():
    import joblib

    if not MODEL_PATH.exists():
        return None, 0.5
    threshold = (
        json.loads(THRESHOLD_PATH.read_text())["threshold"] if THRESHOLD_PATH.exists() else 0.5
    )
    return joblib.load(MODEL_PATH), float(threshold)


@task(name="data-drift-report")
def drift_task(
    reference: pd.DataFrame, current: pd.DataFrame, report_dir: str, config: dict
) -> dict:
    return run_drift_report(reference, current, output_dir=report_dir, config=config)


@task(name="prediction-shift")
def prediction_shift_task(reference: pd.DataFrame, current: pd.DataFrame) -> dict:
    """Compare the high-risk prediction rate in production with the rate the
    same model produces on the training data (apples to apples, same threshold).
    Falls back to the training label rate if the model is unavailable."""
    model, threshold = _load_model_and_threshold()
    if model is not None:
        ref_preds = (model.predict_proba(reference[FEATURE_COLUMNS])[:, 1] >= threshold).astype(int)
        basis = "model predictions on training data"
    else:
        ref_preds = reference[TARGET].astype(int)
        basis = "training label rate (model not found)"
    # Prefer the logged 0/1 prediction; otherwise threshold the logged probability.
    if "prediction" in current.columns:
        cur_preds = pd.to_numeric(current["prediction"]).astype(int)
    else:
        cur_preds = (pd.to_numeric(current["readmission_probability"]) >= threshold).astype(int)
    return {
        "prediction_shift": prediction_distribution_shift(ref_preds, cur_preds),
        "reference_positive_rate": round(float(pd.Series(ref_preds).mean()), 4),
        "current_positive_rate": round(float(cur_preds.mean()), 4),
        "reference_basis": basis,
    }


@task(name="labelled-performance")
def performance_task(labeled: pd.DataFrame | None) -> dict | None:
    """Concept-drift check; only possible once true outcomes are available."""
    if labeled is None or len(labeled) == 0:
        return None
    from src.monitoring.performance import evaluate_with_labels

    model, threshold = _load_model_and_threshold()
    if model is None:
        return None
    return evaluate_with_labels(model, labeled, threshold=threshold)


@task(name="retrain-decision")
def decision_task(
    drift: dict | None,
    shift: dict | None,
    perf: dict | None,
    n_current: int,
    config: dict,
    report_dir: str,
) -> dict:
    min_rows = int(config.get("retraining", {}).get("min_inference_rows", 30))
    if n_current < min_rows:
        retrain, reasons = (
            False,
            [f"only {n_current} inference rows (< {min_rows}); not enough evidence"],
        )
    else:
        retrain, reasons = should_retrain(
            drift_share=drift["dataset_drift_share"],
            current_roc_auc=perf["roc_auc"] if perf else None,
            prediction_shift=shift["prediction_shift"] if shift else None,
            config=config,
        )
    decision = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "retrain": retrain,
        "reasons": reasons,
        "n_current_rows": n_current,
        "drift": drift,
        "prediction_shift": shift,
        "labelled_performance": perf,
    }
    out = Path(report_dir) / "retrain_decision.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    return decision


@flow(name="monitoring-flow")
def monitoring_flow(config_path: str = "configs/monitoring_config.yaml") -> dict:
    config = load_monitoring_config(config_path)
    paths = config["paths"]
    report_dir = paths["report_dir"]

    reference = load_csv(paths["reference_data"])
    current = load_csv(paths["inference_log"])
    if reference is None:
        raise FileNotFoundError(
            f"Reference data missing: {paths['reference_data']} (run training first)"
        )
    if current is None or len(current) == 0:
        print(f"[monitoring] no inference data at {paths['inference_log']} - nothing to monitor")
        return decision_task(None, None, None, 0, config, report_dir)

    drift = drift_task(reference, current, report_dir, config)
    shift = prediction_shift_task(reference, current)
    perf = performance_task(load_csv(paths["labeled_feedback"]))
    decision = decision_task(drift, shift, perf, len(current), config, report_dir)

    print(
        f"[monitoring] drift share={drift['dataset_drift_share']:.2f} "
        f"({drift['n_drifted']}/{drift['n_columns']} columns) drifted={drift['drift_detected']}"
    )
    print(f"[monitoring] prediction shift={shift['prediction_shift']:.3f}  performance={perf}")
    print(f"[monitoring] RETRAIN={decision['retrain']} reasons={decision['reasons']}")
    print(f"[monitoring] report: {drift['report_path']}")
    return decision


if __name__ == "__main__":
    monitoring_flow()
