"""Custom scikit-learn transformers used inside the model Pipeline.

Why custom transformers instead of editing the DataFrame in a notebook?
Because anything done to the data before training MUST be done identically at
prediction time. Putting it inside the sklearn Pipeline guarantees the API and
the training script apply exactly the same steps (no "training/serving skew").

- FeatureEngineer: adds clinically motivated derived columns to the raw DataFrame.
- OutlierClipper: learns per-column lower/upper quantiles on TRAINING data and
  clips extreme values to them (winsorizing), so a single absurd value cannot
  dominate a linear model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

# Names of the columns FeatureEngineer adds, grouped by how they should be preprocessed.
ENGINEERED_NUMERIC = ["pulse_pressure", "utilization_score"]
ENGINEERED_BINARY = ["bp_high", "glucose_abnormal", "polypharmacy"]


class FeatureEngineer(BaseEstimator, TransformerMixin):
    """Add derived features to a raw patient DataFrame.

    - pulse_pressure    = systolic_bp - diastolic_bp (arterial stiffness proxy)
    - bp_high           = 1 if systolic >= 140 or diastolic >= 90 (stage-2 hypertension)
    - glucose_abnormal  = 1 if glucose >= 180 or < 70, or HbA1c >= 6.5 (poor control)
    - utilization_score = previous_admissions + emergency_visits_last_year
    - polypharmacy      = 1 if number_of_medications >= 5 (common clinical definition)

    Missing inputs propagate as NaN so the downstream imputer handles them.
    The transformer is stateless (fit does nothing) — it just returns a copy.
    """

    def __init__(self, polypharmacy_threshold: int = 5):
        self.polypharmacy_threshold = polypharmacy_threshold

    def fit(self, X, y=None):
        return self

    @staticmethod
    def _flag(condition: pd.Series, *inputs: pd.Series) -> pd.Series:
        """Turn a boolean condition into 0/1 floats, keeping NaN where all inputs are missing."""
        out = condition.astype(float)
        all_missing = np.logical_and.reduce([s.isna() for s in inputs])
        out[all_missing] = np.nan
        return out

    def transform(self, X):
        df = pd.DataFrame(X).copy()
        sbp, dbp = df["systolic_bp"].astype(float), df["diastolic_bp"].astype(float)
        glucose, hba1c = df["blood_glucose"].astype(float), df["hba1c"].astype(float)
        meds = df["number_of_medications"].astype(float)

        df["pulse_pressure"] = sbp - dbp
        df["bp_high"] = self._flag((sbp >= 140) | (dbp >= 90), sbp, dbp)
        df["glucose_abnormal"] = self._flag(
            (glucose >= 180) | (glucose < 70) | (hba1c >= 6.5), glucose, hba1c
        )
        df["utilization_score"] = df["previous_admissions"].astype(float) + df[
            "emergency_visits_last_year"
        ].astype(float)
        df["polypharmacy"] = self._flag(meds >= self.polypharmacy_threshold, meds)
        return df

    def get_feature_names_out(self, input_features=None):
        base = list(input_features) if input_features is not None else []
        return np.array(base + ENGINEERED_NUMERIC + ENGINEERED_BINARY, dtype=object)


class OutlierClipper(BaseEstimator, TransformerMixin):
    """Clip each numeric column to [q_low, q_high] quantiles learned on training data.

    Works on numpy arrays (it runs after SimpleImputer inside the ColumnTransformer).
    Default 1st–99th percentile keeps nearly all real variation but caps extremes.
    """

    def __init__(self, lower_quantile: float = 0.01, upper_quantile: float = 0.99):
        self.lower_quantile = lower_quantile
        self.upper_quantile = upper_quantile

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=float)
        self.lower_ = np.nanquantile(X, self.lower_quantile, axis=0)
        self.upper_ = np.nanquantile(X, self.upper_quantile, axis=0)
        self.n_features_in_ = X.shape[1]
        return self

    def transform(self, X):
        X = np.asarray(X, dtype=float)
        return np.clip(X, self.lower_, self.upper_)

    def get_feature_names_out(self, input_features=None):
        return np.asarray(input_features, dtype=object) if input_features is not None else None
