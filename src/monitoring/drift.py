"""Data drift detection with Evidently (v0.7 API).

What this module does
---------------------
"Data drift" means the inputs the model sees in production no longer look like
the data it was trained on (e.g. patients are older, glucose is higher). A model
is only reliable on data that resembles its training data, so we compare:

* reference data = the training split (data/processed/train.csv)
* current data   = recent production inputs (data/inference/inference_log.csv)

For every feature column Evidently runs a statistical test that answers
"do these two samples come from the same distribution?". A column is flagged
as drifted when the test says "no". The *dataset drift share* is the fraction of
columns that drifted; if it is above our threshold (30%, see
configs/monitoring_config.yaml) we say the whole dataset has drifted.

Public functions
----------------
* run_drift_report(reference_df, current_df, output_dir) -> summary dict (CONTRACT)
* prediction_distribution_shift(reference, current) -> abs change in positive rate
* missing_value_report(df) -> per-column missing-value share
* load_monitoring_config(path) -> dict (shared by the other monitoring modules)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from evidently import DataDefinition, Dataset, Report
from evidently.presets import DataDriftPreset

from src.features.schema import (
    BINARY_FEATURES,
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
)

DEFAULT_CONFIG_PATH = "configs/monitoring_config.yaml"


def load_monitoring_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load configs/monitoring_config.yaml as a plain dict."""
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _feature_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Keep only model feature columns, with consistent dtypes.

    Binary (0/1) and categorical columns are converted to strings so Evidently
    treats them as categories (counts per value) rather than as numbers.
    Missing values stay missing.
    """
    out = df[columns].copy()
    for col in columns:
        if col in NUMERIC_FEATURES:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        else:
            # Binary columns may arrive as 0/1 ints or 0.0/1.0 floats -> normalise.
            if col in BINARY_FEATURES:
                out[col] = pd.to_numeric(out[col], errors="coerce").map(
                    lambda v: None if pd.isna(v) else str(int(v))
                )
            else:
                out[col] = out[col].map(lambda v: None if pd.isna(v) else str(v))
    return out


def run_drift_report(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    output_dir: str = "reports/drift",
    config: dict | None = None,
) -> dict:
    """Compare current (production) data with reference (training) data.

    Writes to `output_dir`:
      * drift_report_<timestamp>.html  - the interactive Evidently report
      * drift_summary_<timestamp>.json - our summary + per-column details
      * latest_summary.json            - copy of the newest summary

    Returns the CONTRACT summary dict:
      {"dataset_drift_share", "drifted_columns", "n_drifted", "n_columns",
       "drift_detected", "report_path"}
    """
    if config is None:
        config = load_monitoring_config()
    drift_cfg = config.get("drift", {})
    share_threshold = float(drift_cfg.get("dataset_drift_share_threshold", 0.30))

    # Only compare model features that both datasets actually contain
    # (the inference log also has probability/prediction/timestamp columns).
    columns = [c for c in FEATURE_COLUMNS if c in reference_df.columns and c in current_df.columns]
    if not columns:
        raise ValueError("No common feature columns between reference and current data")
    if len(current_df) == 0:
        raise ValueError("Current data is empty - nothing to compare")

    reference = _feature_frame(reference_df, columns)
    current = _feature_frame(current_df, columns)

    # Tell Evidently which columns are numbers and which are categories; this
    # decides which statistical test is used for each column.
    definition = DataDefinition(
        numerical_columns=[c for c in columns if c in NUMERIC_FEATURES],
        categorical_columns=[
            c for c in columns if c in CATEGORICAL_FEATURES or c in BINARY_FEATURES
        ],
    )

    preset = DataDriftPreset(
        columns=columns,
        drift_share=share_threshold,
        num_method=drift_cfg.get("num_stattest"),
        cat_method=drift_cfg.get("cat_stattest"),
        per_column_method=drift_cfg.get("per_column_stattest") or None,
        threshold=drift_cfg.get("stattest_threshold"),
    )
    # include_tests=True makes Evidently emit a pass/fail "test" per column,
    # which is how we read whether each individual column drifted.
    report = Report([preset], include_tests=True)
    snapshot = report.run(
        current_data=Dataset.from_pandas(current, data_definition=definition),
        reference_data=Dataset.from_pandas(reference, data_definition=definition),
    )
    result = snapshot.dict()

    # Per-column details: test name, score and threshold from each ValueDrift metric.
    column_details: dict[str, dict] = {}
    for metric in result.get("metrics", []):
        cfg = metric.get("config", {})
        if cfg.get("type", "").endswith("ValueDrift"):
            column_details[cfg["column"]] = {
                "stattest": cfg.get("method"),
                "threshold": cfg.get("threshold"),
                "drift_score": float(metric["value"]),
                "drift_detected": False,
            }
    # A FAILED test means "this column drifted".
    for test in result.get("tests", []):
        params = test.get("metric_config", {}).get("params", {})
        col = params.get("column")
        if col in column_details:
            column_details[col]["drift_detected"] = test.get("status") == "FAIL"

    drifted_columns = [c for c in columns if column_details.get(c, {}).get("drift_detected")]
    n_columns = len(columns)
    n_drifted = len(drifted_columns)
    drift_share = n_drifted / n_columns

    # Save HTML + JSON with a timestamp so history is kept.
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    html_path = out_dir / f"drift_report_{stamp}.html"
    snapshot.save_html(str(html_path))

    summary = {
        "dataset_drift_share": round(drift_share, 4),
        "drifted_columns": drifted_columns,
        "n_drifted": n_drifted,
        "n_columns": n_columns,
        "drift_detected": drift_share > share_threshold,
        "report_path": str(html_path),
    }
    details = {
        **summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "drift_share_threshold": share_threshold,
        "n_reference_rows": int(len(reference)),
        "n_current_rows": int(len(current)),
        "columns": column_details,
        "missing_values_current": missing_value_report(current),
    }
    json_text = json.dumps(details, indent=2)
    (out_dir / f"drift_summary_{stamp}.json").write_text(json_text, encoding="utf-8")
    (out_dir / "latest_summary.json").write_text(json_text, encoding="utf-8")
    return summary


def _positive_rate(values, threshold: float) -> float:
    """Share of positives. 0/1 labels are averaged; probabilities are thresholded."""
    arr = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy(dtype=float)
    if arr.size == 0:
        return float("nan")
    is_binary = np.isin(arr, [0.0, 1.0]).all()
    return float(arr.mean()) if is_binary else float((arr >= threshold).mean())


def prediction_distribution_shift(
    reference_probs_or_labels, current_preds, threshold: float = 0.5
) -> float:
    """Absolute change in the positive (high-risk) rate between two samples.

    Each argument may be 0/1 predictions/labels or probabilities (probabilities
    are turned into 0/1 with `threshold`). Example: training predictions were
    18% positive, production predictions are 35% positive -> returns 0.17.
    """
    ref_rate = _positive_rate(reference_probs_or_labels, threshold)
    cur_rate = _positive_rate(current_preds, threshold)
    return round(abs(cur_rate - ref_rate), 4)


def missing_value_report(df: pd.DataFrame) -> dict:
    """Fraction of missing values per column (only columns with any missing),
    plus the overall share across the whole table."""
    if len(df) == 0:
        return {"overall_missing_share": 0.0, "columns": {}}
    shares = df.isna().mean()
    return {
        "overall_missing_share": round(float(df.isna().to_numpy().mean()), 4),
        "columns": {col: round(float(v), 4) for col, v in shares.items() if v > 0},
    }
