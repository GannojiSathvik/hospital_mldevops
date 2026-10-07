"""Prediction service: loads model artifacts once and turns features into predictions.

Responsibilities
----------------
* **Lazy, thread-safe loading** of ``models/readmission_model.joblib`` (one
  sklearn Pipeline: feature engineering + preprocessing + calibrated classifier),
  ``models/threshold.json`` and ``models/model_metadata.json``. If they are
  missing the service reports "not loaded" and ``/ready`` returns 503 instead
  of the API crashing - Kubernetes then simply won't route traffic to it.
* **predict_one / predict_batch** - probability from ``predict_proba``,
  class = probability >= tuned threshold (chosen during training to favour
  recall, not the naive 0.5), risk level from the shared helper
  ``src/models/risk.py``.
* **explain** - SHAP values for one patient: which (transformed) features pushed
  this patient's readmission probability up or down, top-10 by magnitude. We
  use SHAP's model-agnostic permutation explainer on the *preprocessed*
  features against a small background sample of training data, so it works
  whichever model family won training (LogReg, RandomForest, XGBoost...).
  Contributions are in probability units and sum (with the base value) to the
  predicted probability.
* **Inference logging** - every prediction's inputs, probability, class and
  timestamp are appended to ``data/inference/inference_log.csv`` (guarded by a
  lock so concurrent requests don't interleave rows). Drift monitoring compares
  this file against the training data.

joblib/pickle files can execute code when loaded, so we only load artifacts
from our own ``models/`` directory, never from user uploads.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from api.logging_config import get_logger
from api.metrics import (
    HIGH_RISK_PREDICTIONS_TOTAL,
    PREDICTION_LATENCY_SECONDS,
    PREDICTIONS_TOTAL,
)
from src.data.preprocess import read_patient_csv
from src.features.schema import FEATURE_COLUMNS
from src.models.risk import risk_level

logger = get_logger("api.prediction_service")

HIGH_LABEL = "High Risk of 30-Day Readmission"
LOW_LABEL = "Low Risk of 30-Day Readmission"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelNotLoadedError(RuntimeError):
    """Raised when a prediction is requested but artifacts are unavailable."""


class PredictionService:
    def __init__(
        self,
        model_dir: str | None = None,
        inference_log: str | None = None,
        reference_data: str = "data/processed/train.csv",
    ) -> None:
        # Explicit argument wins, then the environment variable, then the default path.
        model_dir_str = model_dir or os.environ.get("MODEL_DIR") or "models"
        inference_log_str = (
            inference_log
            or os.environ.get("INFERENCE_LOG_PATH")
            or "data/inference/inference_log.csv"
        )
        self.model_dir = Path(model_dir_str)
        self.inference_log = Path(inference_log_str)
        self.reference_data = Path(reference_data)
        self.model: Any = None
        self.threshold: float = 0.5
        self.threshold_strategy: str | None = None
        self.metadata: dict = {}
        self.load_error: str | None = None
        self._explainer: Any = None
        self._load_lock = threading.Lock()
        self._log_lock = threading.Lock()

    # ------------------------------------------------------------------ loading
    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    @property
    def model_version(self) -> str:
        return str(self.metadata.get("model_version", "unknown"))

    def load(self) -> bool:
        """Load artifacts if not already loaded. Returns True on success."""
        if self.model is not None:
            return True
        with self._load_lock:
            if self.model is not None:
                return True
            try:
                model = joblib.load(self.model_dir / "readmission_model.joblib")
                thr_path = self.model_dir / "threshold.json"
                if thr_path.exists():
                    thr = json.loads(thr_path.read_text())
                    self.threshold = float(thr["threshold"])
                    self.threshold_strategy = thr.get("strategy")
                meta_path = self.model_dir / "model_metadata.json"
                if meta_path.exists():
                    self.metadata = json.loads(meta_path.read_text())
                self.model = model
                self.load_error = None
            except Exception as exc:  # noqa: BLE001 - report as "not ready"
                # Store the exception *type* only; details go to server logs.
                self.load_error = type(exc).__name__
                self.model = None
        return self.model is not None

    def _require_model(self) -> None:
        if not self.load():
            raise ModelNotLoadedError("Model artifacts are not available")

    # -------------------------------------------------------------- prediction
    @staticmethod
    def to_frame(records: list[dict]) -> pd.DataFrame:
        """Build a DataFrame with exactly the 25 feature columns, in order."""
        return pd.DataFrame.from_records(records).reindex(columns=FEATURE_COLUMNS)

    def _result(self, prob: float, ts: str) -> dict:
        pred = int(prob >= self.threshold)
        return {
            "prediction": pred,
            "prediction_label": HIGH_LABEL if pred else LOW_LABEL,
            "readmission_probability": round(float(prob), 4),
            "risk_level": risk_level(float(prob), self.threshold),
            "model_version": self.model_version,
            "timestamp": ts,
        }

    def _score(self, df: pd.DataFrame, endpoint: str) -> list[dict]:
        self._require_model()
        start = time.perf_counter()
        probs = self.model.predict_proba(df)[:, 1]
        PREDICTION_LATENCY_SECONDS.observe(time.perf_counter() - start)
        ts = _utc_now()
        results = [self._result(p, ts) for p in probs]
        for r in results:
            PREDICTIONS_TOTAL.labels(endpoint=endpoint, risk_level=r["risk_level"]).inc()
            if r["risk_level"] == "High":
                HIGH_RISK_PREDICTIONS_TOTAL.inc()
        self._log_inference(df, results)
        return results

    def predict_one(self, features: dict, endpoint: str = "/predict") -> dict:
        return self._score(self.to_frame([features]), endpoint)[0]

    def predict_batch(self, df: pd.DataFrame) -> list[dict]:
        return self._score(df.reindex(columns=FEATURE_COLUMNS), "/predict/batch")

    # ------------------------------------------------------------ explanation
    def _split_pipeline(self):
        """Return (transform_fn, final_estimator, feature_names)."""
        model = self.model
        if hasattr(model, "steps") and len(model.steps) > 1:
            pre, est = model[:-1], model[-1]
        else:  # not a Pipeline: fall back to the standalone preprocessor
            pre, est = joblib.load(self.model_dir / "preprocessor.joblib"), model
        # Column names after preprocessing (e.g. "num__age", "cat__gender_Male").
        # Asking the whole chain can fail (the custom FeatureEngineer step doesn't
        # propagate names), so fall back to the last step - the ColumnTransformer,
        # which remembers its own output names. Only if both fail use f0, f1, ...
        names = None
        for source in (pre, getattr(pre, "steps", [[None, None]])[-1][1]):
            try:
                names = [str(n) for n in source.get_feature_names_out()]
                break
            except Exception as exc:  # noqa: BLE001 - try the next source
                logger.debug("get_feature_names_out failed on %s: %s", type(source).__name__, exc)

        def transform(df: pd.DataFrame) -> np.ndarray:
            out = pre.transform(df)
            if hasattr(out, "toarray"):
                out = out.toarray()
            return np.asarray(out, dtype=float)

        return transform, est, names

    def _get_explainer(self):
        if self._explainer is None:
            import shap  # imported lazily: heavy, only needed for /predict/explain

            transform, est, names = self._split_pipeline()
            if not self.reference_data.exists():
                raise ModelNotLoadedError("Background data for SHAP is unavailable")
            bg_raw = read_patient_csv(self.reference_data).reindex(columns=FEATURE_COLUMNS)
            background = transform(bg_raw.sample(min(100, len(bg_raw)), random_state=42))
            n_features = background.shape[1]
            names = names or [f"f{i}" for i in range(n_features)]

            def f(x: np.ndarray) -> np.ndarray:
                return est.predict_proba(x)[:, 1]

            explainer = shap.Explainer(
                f,
                shap.maskers.Independent(background, max_samples=100),
                algorithm="permutation",
                feature_names=names,
                seed=42,
            )
            self._explainer = (explainer, transform, names, n_features)
        return self._explainer

    def explain(self, features: dict, top_k: int = 10) -> dict:
        self._require_model()
        result = self.predict_one(features, endpoint="/predict/explain")
        explainer, transform, names, n_features = self._get_explainer()
        x = transform(self.to_frame([features]))
        sv = explainer(x, max_evals=max(500, 2 * n_features + 1), silent=True)
        values = np.asarray(sv.values[0], dtype=float)
        order = np.argsort(-np.abs(values))[:top_k]
        result["top_features"] = [
            {
                "feature": names[i],
                "value": round(float(x[0, i]), 4),
                "contribution": round(float(values[i]), 4),
            }
            for i in order
        ]
        result["explanation_method"] = "shap-permutation (probability units)"
        return result

    # ---------------------------------------------------------- inference log
    def _log_inference(self, df: pd.DataFrame, results: list[dict]) -> None:
        """Append inputs + outputs to the inference log CSV (thread-safe)."""
        out = df.reindex(columns=FEATURE_COLUMNS).copy()
        out["readmission_probability"] = [r["readmission_probability"] for r in results]
        out["prediction"] = [r["prediction"] for r in results]
        out["risk_level"] = [r["risk_level"] for r in results]
        out["model_version"] = [r["model_version"] for r in results]
        out["timestamp"] = [r["timestamp"] for r in results]
        try:
            with self._log_lock:
                self.inference_log.parent.mkdir(parents=True, exist_ok=True)
                write_header = not self.inference_log.exists()
                out.to_csv(self.inference_log, mode="a", header=write_header, index=False)
        except OSError:
            # Logging failure must not break predictions (e.g. read-only FS);
            # the error is visible in server logs via the caller's logger.
            import logging

            logging.getLogger("api.prediction_service").warning("Could not write inference log")


# One shared instance for the app (loaded lazily on first use / at startup).
service = PredictionService()
