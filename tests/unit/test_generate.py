"""Tests for the synthetic data generator: schema, size, reproducibility, realism."""

import pandas as pd

from src.data.generate import SAMPLE_INPUT, generate_dataset, generate_sample_batch
from src.data.validation import validate_dataframe
from src.features.schema import FEATURE_COLUMNS, ID_COLUMN, TARGET


def test_columns_and_size():
    df = generate_dataset(n_rows=1000, seed=1)
    assert list(df.columns) == [ID_COLUMN] + FEATURE_COLUMNS + [TARGET]
    assert len(df) == 1000
    assert df[ID_COLUMN].is_unique
    assert df[ID_COLUMN].iloc[0] == "P000001"


def test_reproducible_with_same_seed():
    pd.testing.assert_frame_equal(
        generate_dataset(n_rows=300, seed=7), generate_dataset(n_rows=300, seed=7)
    )


def test_class_imbalance_and_missing_values():
    df = generate_dataset(n_rows=5000, seed=42)
    assert 0.13 <= df[TARGET].mean() <= 0.22
    missing = df[FEATURE_COLUMNS].isna().mean()
    assert 0.02 <= missing.max() <= 0.06
    assert (missing > 0).sum() >= 3


def test_realistic_relationships():
    df = generate_dataset(n_rows=5000, seed=42)
    # More previous admissions -> higher readmission rate; follow-up is protective.
    assert (
        df[df.previous_admissions >= 3][TARGET].mean()
        > df[df.previous_admissions == 0][TARGET].mean()
    )
    assert (
        df[df.follow_up_scheduled == 0][TARGET].mean()
        > df[df.follow_up_scheduled == 1][TARGET].mean()
    )


def test_generated_data_passes_validation():
    assert validate_dataframe(generate_dataset(n_rows=2000, seed=3)).is_valid


def test_sample_inputs():
    assert set(SAMPLE_INPUT) == set(FEATURE_COLUMNS)
    batch = generate_sample_batch()
    assert len(batch) == 20 and TARGET not in batch.columns and not batch.isna().any().any()
