"""Data validation tests on one valid dataset and several deliberately broken ones."""

import numpy as np
import pytest

from src.data.generate import generate_dataset
from src.data.validation import validate_dataframe
from src.features.schema import TARGET


@pytest.fixture(scope="module")
def valid_df():
    return generate_dataset(n_rows=1000, seed=11)


def test_valid_dataset_passes(valid_df):
    result = validate_dataframe(valid_df)
    assert result.is_valid, result.errors
    assert any("imbalance" in w.lower() for w in result.warnings)


def test_missing_required_column(valid_df):
    result = validate_dataframe(valid_df.drop(columns=["age"]))
    assert not result.is_valid
    assert any("Missing required columns" in e and "age" in e for e in result.errors)


def test_bad_category(valid_df):
    df = valid_df.copy()
    df.loc[0, "gender"] = "Unknown"
    result = validate_dataframe(df)
    assert any("gender" in e and "Unknown" in e for e in result.errors)


def test_out_of_range_value(valid_df):
    df = valid_df.copy()
    df.loc[0, "age"] = 250
    assert any("age" in e and "range" in e for e in validate_dataframe(df).errors)


def test_duplicate_patient_ids(valid_df):
    df = valid_df.copy()
    df.loc[1, "patient_id"] = df.loc[0, "patient_id"]
    assert any("duplicate" in e for e in validate_dataframe(df).errors)


def test_leakage_by_correlation(valid_df):
    df = valid_df.copy()
    df["discharge_code"] = df[TARGET] * 10 + 1  # a column that IS the label in disguise
    result = validate_dataframe(df)
    assert any("leakage" in e and "discharge_code" in e for e in result.errors)
    assert any("unexpected columns" in w for w in result.warnings)  # schema change also reported


def test_leakage_by_name(valid_df):
    df = valid_df.copy()
    df["readmission_flag"] = np.random.default_rng(0).integers(0, 2, len(df))
    assert any("leakage" in e and "readmission_flag" in e for e in validate_dataframe(df).errors)


def test_too_many_missing(valid_df):
    df = valid_df.copy()
    df.loc[: int(len(df) * 0.5), "bmi"] = np.nan
    assert any("bmi" in e and "missing" in e for e in validate_dataframe(df).errors)


def test_wrong_dtype_and_binary(valid_df):
    df = valid_df.copy()
    df["heart_rate"] = df["heart_rate"].astype(str)
    df["diabetes"] = df["diabetes"].replace({1: 2})
    errors = validate_dataframe(df).errors
    assert any("heart_rate" in e and "numeric" in e for e in errors)
    assert any("diabetes" in e for e in errors)


def test_bad_target(valid_df):
    df = valid_df.copy()
    df[TARGET] = 0
    assert any("one class" in e for e in validate_dataframe(df).errors)


def test_inference_data_without_target(valid_df):
    assert validate_dataframe(valid_df.drop(columns=[TARGET]), require_target=False).is_valid
