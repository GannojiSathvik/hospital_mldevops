"""Simulate production traffic so the drift-monitoring demo has data to look at.

In a real deployment the API appends every prediction to
data/inference/inference_log.csv. In the lab there is no real traffic, so this
script fakes it:

  --mode normal   rows sampled from the held-out test split -> should look like
                  the training data (little or no drift expected)
  --mode drifted  the same rows, deliberately shifted to mimic a changed patient
                  population: older patients, higher blood glucose and HbA1c,
                  more emergency admissions, fewer follow-up appointments
                  -> drift should be detected and retraining triggered

The trained model (models/readmission_model.joblib) fills in
readmission_probability and prediction exactly like the API would, so the log
has the same columns the API writes:
  FEATURE_COLUMNS + readmission_probability + prediction + risk_level
  + model_version + timestamp

--with-labels also writes data/inference/labeled_feedback.csv: the same
patients with a (simulated) true outcome, as if follow-up data had arrived 30
days later. This enables the concept-drift (ROC-AUC) check.

Usage:
  python -m scripts.seed_monitoring_data --mode normal
  python -m scripts.seed_monitoring_data --mode drifted --with-labels
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.data.preprocess import read_patient_csv
from src.features.schema import FEATURE_COLUMNS, NUMERIC_RANGES, TARGET
from src.models.risk import risk_level

TEST_PATH = Path("data/processed/test.csv")
MODEL_PATH = Path("models/readmission_model.joblib")
THRESHOLD_PATH = Path("models/threshold.json")
METADATA_PATH = Path("models/model_metadata.json")
LOG_PATH = Path("data/inference/inference_log.csv")
FEEDBACK_PATH = Path("data/inference/labeled_feedback.csv")


def apply_drift(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Shift the population to simulate a different (sicker, older) patient mix."""
    df = df.copy()
    n = len(df)
    df["age"] = df["age"] + 15
    df["blood_glucose"] = df["blood_glucose"] * 1.35
    df["hba1c"] = df["hba1c"] + 1.8
    df["systolic_bp"] = df["systolic_bp"] + 15
    df["previous_admissions"] = df["previous_admissions"] + rng.integers(1, 4, n)
    df["emergency_visits_last_year"] = df["emergency_visits_last_year"] + rng.integers(1, 4, n)
    df["chronic_disease_count"] = df["chronic_disease_count"] + 1
    df["length_of_stay"] = df["length_of_stay"] + 3
    # 75% of admissions become emergencies; 75% lose their follow-up appointment.
    df.loc[rng.random(n) < 0.75, "admission_type"] = "Emergency"
    df.loc[rng.random(n) < 0.75, "follow_up_scheduled"] = 0
    df.loc[rng.random(n) < 0.5, "insurance_type"] = "Medicare"
    df.loc[rng.random(n) < 0.4, "discharge_destination"] = "Skilled Nursing Facility"
    df.loc[rng.random(n) < 0.4, "diabetes"] = 1

    # Keep values inside the valid ranges defined by the schema.
    for col, (lo, hi) in NUMERIC_RANGES.items():
        if col in df.columns:
            df[col] = df[col].clip(lo, hi)
    return df


def simulate_labels(df: pd.DataFrame, mode: str, rng: np.random.Generator) -> pd.Series:
    """Simulated true outcomes. Normal: the real test labels. Drifted: real
    labels with 30% of them flipped, mimicking a changed input->outcome
    relationship (concept drift) so the model's ROC-AUC drops."""
    y = df[TARGET].astype(int).copy()
    if mode == "drifted":
        flip = rng.random(len(y)) < 0.30
        y[flip] = 1 - y[flip]
    return y


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=["normal", "drifted"], default="normal")
    parser.add_argument("--rows", type=int, default=500, help="number of simulated predictions")
    parser.add_argument(
        "--with-labels", action="store_true", help="also write labeled_feedback.csv"
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    test = read_patient_csv(TEST_PATH)
    sample = test.sample(
        n=args.rows, replace=args.rows > len(test), random_state=args.seed
    ).reset_index(drop=True)
    if args.mode == "drifted":
        sample = apply_drift(sample, rng)

    model = joblib.load(MODEL_PATH)
    threshold = (
        json.loads(THRESHOLD_PATH.read_text())["threshold"] if THRESHOLD_PATH.exists() else 0.5
    )
    probs = model.predict_proba(sample[FEATURE_COLUMNS])[:, 1]

    log = sample[FEATURE_COLUMNS].copy()
    log["readmission_probability"] = np.round(probs, 6)
    log["prediction"] = (probs >= threshold).astype(int)
    # Same extra columns, in the same order, as api/prediction_service.py writes,
    # so the API can keep appending to this file after seeding.
    log["risk_level"] = [risk_level(float(p), threshold) for p in probs]
    log["model_version"] = (
        json.loads(METADATA_PATH.read_text()).get("model_version", "unknown")
        if METADATA_PATH.exists()
        else "unknown"
    )
    # Spread timestamps over the last 24 hours, oldest first.
    now = datetime.now(timezone.utc)
    offsets = np.sort(rng.uniform(0, 24 * 3600, len(log)))[::-1]
    log["timestamp"] = [(now - timedelta(seconds=float(s))).isoformat() for s in offsets]

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.to_csv(LOG_PATH, index=False)  # overwrite: each run is a fresh demo scenario
    print(
        f"[seed] wrote {len(log)} {args.mode} rows to {LOG_PATH} "
        f"(high-risk rate {log['prediction'].mean():.3f}, threshold {threshold:.3f})"
    )

    if args.with_labels:
        feedback = sample[FEATURE_COLUMNS].copy()
        feedback[TARGET] = simulate_labels(sample, args.mode, rng).to_numpy()
        feedback.to_csv(FEEDBACK_PATH, index=False)
        print(f"[seed] wrote {len(feedback)} labelled rows to {FEEDBACK_PATH}")
    elif FEEDBACK_PATH.exists():
        # Remove stale labels from a previous scenario so results stay consistent.
        FEEDBACK_PATH.unlink()
        print(f"[seed] removed stale {FEEDBACK_PATH}")


if __name__ == "__main__":
    main()
