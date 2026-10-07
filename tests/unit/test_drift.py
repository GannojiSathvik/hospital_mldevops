"""Unit tests for src/monitoring/drift.py (Evidently data-drift wrapper)."""

import json

import numpy as np
import pandas as pd
import pytest

from src.features.schema import (
    BINARY_FEATURES,
    CATEGORY_VALUES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
)
from src.monitoring.drift import (
    missing_value_report,
    prediction_distribution_shift,
    run_drift_report,
)

CONFIG = {"drift": {"dataset_drift_share_threshold": 0.30}}
CONTRACT_KEYS = {
    "dataset_drift_share",
    "drifted_columns",
    "n_drifted",
    "n_columns",
    "drift_detected",
    "report_path",
}


def make_frame(n: int, seed: int, shift: bool = False) -> pd.DataFrame:
    """Synthetic patients with all 25 feature columns; `shift` moves every column."""
    rng = np.random.default_rng(seed)
    data = {}
    for col in NUMERIC_FEATURES:
        data[col] = rng.normal(100 if shift else 50, 10, n)
    for col in BINARY_FEATURES:
        data[col] = rng.binomial(1, 0.9 if shift else 0.2, n)
    for col, values in CATEGORY_VALUES.items():
        probs = np.full(len(values), 0.05 / (len(values) - 1))
        probs[-1 if shift else 0] = 0.95
        data[col] = rng.choice(values, n, p=probs)
    return pd.DataFrame(data)[FEATURE_COLUMNS]


def test_identical_data_has_no_drift(tmp_path):
    ref = make_frame(600, seed=1)
    summary = run_drift_report(ref, ref.copy(), output_dir=str(tmp_path), config=CONFIG)
    assert summary["n_drifted"] == 0
    assert summary["dataset_drift_share"] == 0.0
    assert summary["drift_detected"] is False


def test_shifted_data_is_detected(tmp_path):
    ref = make_frame(600, seed=1)
    cur = make_frame(300, seed=2, shift=True)
    summary = run_drift_report(ref, cur, output_dir=str(tmp_path), config=CONFIG)
    assert summary["drift_detected"] is True
    assert summary["dataset_drift_share"] > 0.30
    assert "age" in summary["drifted_columns"]


def test_summary_keys_and_files(tmp_path):
    ref = make_frame(400, seed=1)
    # Extra non-feature columns (as in the inference log) must be ignored.
    cur = make_frame(200, seed=3).assign(readmission_probability=0.2, prediction=0, timestamp="t")
    summary = run_drift_report(ref, cur, output_dir=str(tmp_path), config=CONFIG)
    assert set(summary) == CONTRACT_KEYS
    assert summary["n_columns"] == len(FEATURE_COLUMNS)
    assert (tmp_path / "latest_summary.json").exists()
    assert summary["report_path"].endswith(".html")
    assert (tmp_path / summary["report_path"].split("/")[-1]).exists()
    saved = json.loads((tmp_path / "latest_summary.json").read_text())
    assert saved["n_drifted"] == summary["n_drifted"]


def test_empty_current_data_raises(tmp_path):
    ref = make_frame(100, seed=1)
    with pytest.raises(ValueError):
        run_drift_report(ref, ref.iloc[0:0], output_dir=str(tmp_path), config=CONFIG)


def test_prediction_distribution_shift():
    assert prediction_distribution_shift([0, 0, 1, 1], [0, 1, 1, 1]) == 0.25
    # probabilities are thresholded at 0.5 by default
    assert prediction_distribution_shift([0.1, 0.9], [0.8, 0.9]) == 0.5


def test_missing_value_report():
    df = pd.DataFrame({"a": [1, None, 3, None], "b": [1, 2, 3, 4]})
    report = missing_value_report(df)
    assert report["columns"] == {"a": 0.5}
    assert report["overall_missing_share"] == 0.25
