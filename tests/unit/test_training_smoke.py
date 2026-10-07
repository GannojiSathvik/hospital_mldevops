"""Fast end-to-end training smoke test on ~400 synthetic rows (no MLflow, no SHAP).

Checks that every contract artifact is produced and the saved model can score raw input.
"""

import json

import joblib
import pandas as pd
import pytest

from src.data.generate import SAMPLE_INPUT, generate_dataset
from src.data.preprocess import split_dataset
from src.features.schema import FEATURE_COLUMNS
from src.models.train import load_yaml, run_training


@pytest.mark.filterwarnings("ignore")
def test_training_smoke(tmp_path):
    raw = tmp_path / "raw.csv"
    generate_dataset(n_rows=400, seed=9).to_csv(raw, index=False)
    train_p, test_p = tmp_path / "train.csv", tmp_path / "test.csv"
    split_dataset(raw, train_p, test_p, test_size=0.25, random_state=42)

    params = load_yaml("params.yaml")
    params["train"].update(n_iter=2, shap_sample_size=0)
    models_dir = tmp_path / "models"
    summary = run_training(
        str(train_p),
        str(test_p),
        str(models_dir),
        str(tmp_path / "fig"),
        str(tmp_path / "eval"),
        params=params,
        log_to_mlflow=False,
        trained_by="pytest",
    )

    for name in [
        "readmission_model.joblib",
        "preprocessor.joblib",
        "threshold.json",
        "model_metadata.json",
        "metrics.json",
        "feature_list.json",
    ]:
        assert (models_dir / name).exists(), name

    meta = json.loads((models_dir / "model_metadata.json").read_text())
    for key in [
        "model_name",
        "model_version",
        "trained_at",
        "feature_columns",
        "threshold",
        "metrics",
        "training_data_hash",
        "trained_by",
    ]:
        assert key in meta
    assert meta["trained_by"] == "pytest" and meta["model_version"] == "1.0.0"
    assert json.loads((models_dir / "feature_list.json").read_text()) == FEATURE_COLUMNS
    assert set(json.loads((models_dir / "threshold.json").read_text())) == {"threshold", "strategy"}

    model = joblib.load(models_dir / "readmission_model.joblib")
    p = model.predict_proba(pd.DataFrame([SAMPLE_INPUT]))[:, 1][0]
    assert 0.0 <= p <= 1.0
    assert summary["metrics"]["roc_auc"] > 0.5
