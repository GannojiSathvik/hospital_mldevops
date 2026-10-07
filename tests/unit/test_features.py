"""Tests for the custom transformers and the preprocessing pipeline."""

import numpy as np
import pandas as pd

from src.data.generate import SAMPLE_INPUT, generate_dataset
from src.features.engineering import (
    ENGINEERED_BINARY,
    ENGINEERED_NUMERIC,
    FeatureEngineer,
    OutlierClipper,
)
from src.features.schema import FEATURE_COLUMNS
from src.models.pipeline_factory import build_preprocessor


def test_feature_engineer_values():
    out = FeatureEngineer().fit_transform(pd.DataFrame([SAMPLE_INPUT]))
    row = out.iloc[0]
    assert row["pulse_pressure"] == 145 - 92
    assert row["bp_high"] == 1  # 145/92 is stage-2 hypertension
    assert row["glucose_abnormal"] == 1  # HbA1c 8.2
    assert row["utilization_score"] == 3 + 2
    assert row["polypharmacy"] == 1  # 7 medications
    for col in ENGINEERED_NUMERIC + ENGINEERED_BINARY:
        assert col in out.columns


def test_feature_engineer_propagates_missing():
    sample = dict(SAMPLE_INPUT, blood_glucose=np.nan, hba1c=np.nan)
    out = FeatureEngineer().transform(pd.DataFrame([sample]))
    assert np.isnan(out.loc[0, "glucose_abnormal"])


def test_outlier_clipper():
    X = np.arange(100, dtype=float).reshape(-1, 1)
    X[-1, 0] = 10_000
    clipped = OutlierClipper(0.01, 0.99).fit(X).transform(X)
    assert clipped.max() < 1000
    assert clipped.min() >= X.min()


def test_preprocessor_handles_missing_and_unknown_category():
    train = generate_dataset(n_rows=300, seed=5)[FEATURE_COLUMNS]
    pre = build_preprocessor()
    fe = FeatureEngineer()
    pre.fit(fe.transform(train))
    new = pd.DataFrame([dict(SAMPLE_INPUT, gender="Unknown", bmi=np.nan)])
    Xt = pre.transform(fe.transform(new))
    assert Xt.shape[0] == 1 and not np.isnan(Xt).any()
