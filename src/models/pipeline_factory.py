"""Builds the end-to-end scikit-learn Pipeline (raw DataFrame -> probability).

    raw 25 columns
      -> FeatureEngineer          (adds pulse_pressure, bp_high, glucose_abnormal, utilization_score, polypharmacy)
      -> ColumnTransformer
           numeric:     SimpleImputer(median) -> OutlierClipper(1%-99%) -> StandardScaler
           binary:      SimpleImputer(most_frequent)
           categorical: SimpleImputer(most_frequent) -> OneHotEncoder(handle_unknown="ignore")
      -> classifier               (LogReg / RandomForest / XGBoost, class-imbalance aware)

Because every step is inside ONE Pipeline object, `fit` learns imputation
values, clip limits, scaling and categories from training data only, and the
saved .joblib applies exactly the same transformations at prediction time.
handle_unknown="ignore" means an unseen category at inference becomes all
zeros instead of crashing the API.
"""

from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from src.features.engineering import (
    ENGINEERED_BINARY,
    ENGINEERED_NUMERIC,
    FeatureEngineer,
    OutlierClipper,
)
from src.features.schema import (
    BINARY_FEATURES,
    CATEGORICAL_FEATURES,
    CATEGORY_VALUES,
    NUMERIC_FEATURES,
)

MODEL_NAMES = ["logistic_regression", "random_forest", "xgboost"]


def build_preprocessor(
    lower_quantile: float = 0.01, upper_quantile: float = 0.99
) -> ColumnTransformer:
    """ColumnTransformer that turns the (engineered) DataFrame into a numeric matrix."""
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("clip", OutlierClipper(lower_quantile, upper_quantile)),
            ("scale", StandardScaler()),
        ]
    )
    binary = Pipeline([("impute", SimpleImputer(strategy="most_frequent"))])
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            (
                "onehot",
                OneHotEncoder(
                    categories=[CATEGORY_VALUES[c] for c in CATEGORICAL_FEATURES],
                    handle_unknown="ignore",
                    sparse_output=False,
                ),
            ),
        ]
    )
    return ColumnTransformer(
        [
            ("num", numeric, NUMERIC_FEATURES + ENGINEERED_NUMERIC),
            ("bin", binary, BINARY_FEATURES + ENGINEERED_BINARY),
            ("cat", categorical, CATEGORICAL_FEATURES),
        ],
        remainder="drop",  # patient_id or any unexpected column is ignored
        verbose_feature_names_out=True,
    )


def build_classifier(name: str, random_state: int = 42, scale_pos_weight: float = 1.0):
    """Return an untuned classifier with class-imbalance handling switched on."""
    if name == "logistic_regression":
        return LogisticRegression(class_weight="balanced", max_iter=2000, random_state=random_state)
    if name == "random_forest":
        return RandomForestClassifier(class_weight="balanced", n_jobs=1, random_state=random_state)
    if name == "xgboost":
        return XGBClassifier(
            scale_pos_weight=scale_pos_weight,  # = n_negative / n_positive
            eval_metric="logloss",
            tree_method="hist",
            n_jobs=1,
            random_state=random_state,
        )
    raise ValueError(f"Unknown model name: {name}")


def build_pipeline(
    classifier, lower_quantile: float = 0.01, upper_quantile: float = 0.99
) -> Pipeline:
    """Full pipeline: feature engineering -> preprocessing -> classifier."""
    return Pipeline(
        [
            ("features", FeatureEngineer()),
            ("preprocessor", build_preprocessor(lower_quantile, upper_quantile)),
            ("classifier", classifier),
        ]
    )
