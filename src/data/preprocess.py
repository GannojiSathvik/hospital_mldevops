"""Validate the raw dataset, then split it into train/test files.

We use a STRATIFIED split: the 80/20 split keeps the same readmission rate
(~17%) in both train and test, so test metrics are comparable to training.
The fixed random_state makes the split reproducible (same rows every run).
Note: imputation/scaling are NOT done here — they live inside the model
Pipeline so they are learned only from training data (no test leakage) and
re-applied identically at prediction time.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

from src.data.validation import validate_dataframe
from src.features.schema import TARGET

# IMPORTANT: "None" is a legitimate alcohol_consumption category, but pandas treats
# the string "None" as missing by default. Always read patient CSVs with these
# options so only empty cells (and explicit NaN/NA/null markers) become missing.
NA_VALUES = ["", "NA", "N/A", "NaN", "nan", "null", "NULL"]
READ_CSV_KWARGS = {"keep_default_na": False, "na_values": NA_VALUES}


def read_patient_csv(path_or_buffer) -> pd.DataFrame:
    """pd.read_csv that keeps the 'None' category intact (use everywhere patient CSVs are read)."""
    return pd.read_csv(path_or_buffer, **READ_CSV_KWARGS)


def load_raw(path: str | Path) -> pd.DataFrame:
    return read_patient_csv(path)


def split_dataset(
    raw_path: str | Path,
    train_path: str | Path,
    test_path: str | Path,
    test_size: float = 0.2,
    random_state: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate raw data (raise on errors), stratified split, write CSVs."""
    df = load_raw(raw_path)
    result = validate_dataframe(df, require_target=True)
    print(result.summary())
    if not result.is_valid:
        raise ValueError("Raw data failed validation; refusing to create train/test splits")

    train_df, test_df = train_test_split(
        df, test_size=test_size, stratify=df[TARGET], random_state=random_state
    )
    for frame, path in ((train_df, train_path), (test_df, test_path)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
    return train_df, test_df
