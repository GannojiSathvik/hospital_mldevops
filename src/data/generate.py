"""Synthetic healthcare readmission dataset generator.

We are not allowed to use real patient data, so we SIMULATE patients. The key
idea: first draw patient characteristics with sensible correlations (older
people have more hypertension, diabetics have higher glucose, more chronic
diseases -> more medications/admissions...), then compute a readmission
probability with a LOGISTIC function of clinically motivated risk factors:

    logit = intercept + 0.45*previous_admissions + 0.30*emergency_visits
            + 0.22*chronic_disease_count + 0.06*length_of_stay
            + 0.55*abnormal_glucose - 0.90*follow_up_scheduled + ...
    p = 1 / (1 + exp(-logit)),   readmitted ~ Bernoulli(p)

The intercept is solved numerically so ~17% of patients are readmitted
(class imbalance like real hospitals). Finally we inject ~4% missing values
into a few columns and a small number of (plausible but extreme) outliers, so
the preprocessing pipeline has realistic work to do. Everything is driven by a
seeded numpy Generator => same seed, same dataset (reproducibility).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.schema import FEATURE_COLUMNS, ID_COLUMN, TARGET

# Columns that receive random missing values, and columns that get outliers.
MISSING_COLUMNS = [
    "bmi",
    "blood_glucose",
    "hba1c",
    "cholesterol",
    "smoking_status",
    "alcohol_consumption",
]
OUTLIER_SPECS = {  # column -> (low, high) range the outlier value is drawn from
    "bmi": (55.0, 70.0),
    "blood_glucose": (380.0, 550.0),
    "length_of_stay": (40, 75),
    "cholesterol": (380.0, 480.0),
}


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _solve_intercept(base_logit: np.ndarray, target_rate: float) -> float:
    """Bisection: find intercept b so that mean(sigmoid(base_logit + b)) == target_rate."""
    lo, hi = -20.0, 20.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _sigmoid(base_logit + mid).mean() > target_rate:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def generate_dataset(
    n_rows: int = 6000,
    seed: int = 42,
    positive_rate: float = 0.17,
    missing_rate: float = 0.04,
    outlier_rate: float = 0.005,
    id_offset: int = 0,
    include_target: bool = True,
) -> pd.DataFrame:
    """Return a DataFrame with patient_id, the 25 features and (optionally) the target."""
    rng = np.random.default_rng(seed)
    n = n_rows

    # --- Demographics ---------------------------------------------------------
    age = np.clip(rng.normal(64, 15, n), 18, 100).round().astype(int)
    gender = rng.choice(["Male", "Female", "Other"], n, p=[0.48, 0.50, 0.02])
    bmi = np.clip(rng.normal(28.5, 5.5, n), 15, 52).round(1)

    # --- Chronic conditions (more likely with age / BMI) ----------------------
    hypertension = rng.binomial(1, _sigmoid(-0.6 + 0.05 * (age - 60) + 0.05 * (bmi - 28)))
    diabetes = rng.binomial(1, _sigmoid(-1.3 + 0.03 * (age - 60) + 0.10 * (bmi - 28)))
    heart_disease = rng.binomial(1, _sigmoid(-1.8 + 0.05 * (age - 60) + 0.5 * hypertension))
    kidney_disease = rng.binomial(1, _sigmoid(-2.5 + 0.04 * (age - 60) + 0.8 * diabetes))
    chronic_disease_count = np.clip(
        hypertension + diabetes + heart_disease + kidney_disease + rng.poisson(0.8, n), 0, 12
    )

    # --- Vitals & labs ---------------------------------------------------------
    systolic_bp = np.clip(
        120 + 18 * hypertension + 0.3 * (age - 60) + rng.normal(0, 14, n), 85, 220
    ).round()
    diastolic_bp = np.clip(75 + 0.35 * (systolic_bp - 120) + rng.normal(0, 8, n), 45, 130).round()
    heart_rate = np.clip(rng.normal(78, 12, n) + 4 * heart_disease, 45, 150).round()
    blood_glucose = np.clip(
        98 + 75 * diabetes + rng.gamma(2.0, 12, n) - 20 + rng.normal(0, 18, n), 60, 350
    ).round()
    hba1c = np.clip(
        5.2 + 1.8 * diabetes + 0.008 * (blood_glucose - 100) + rng.normal(0, 0.5, n), 4.0, 14.0
    ).round(1)
    cholesterol = np.clip(rng.normal(198, 38, n) + 10 * heart_disease, 110, 350).round()

    # --- Utilization history --------------------------------------------------
    number_of_medications = np.clip(rng.poisson(1.5 + 1.3 * chronic_disease_count), 0, 30)
    previous_admissions = np.clip(
        rng.poisson(0.3 + 0.35 * chronic_disease_count + 0.01 * np.maximum(age - 50, 0)), 0, 15
    )
    emergency_visits_last_year = np.clip(
        rng.poisson(0.3 + 0.25 * chronic_disease_count + 0.2 * previous_admissions), 0, 15
    )
    admission_type = rng.choice(["Emergency", "Elective", "Urgent"], n, p=[0.50, 0.22, 0.28])
    length_of_stay = np.clip(
        1 + rng.poisson(2.5 + 0.6 * chronic_disease_count + 1.5 * (admission_type == "Emergency")),
        1,
        30,
    )

    # --- Lifestyle / social --------------------------------------------------
    smoking_status = rng.choice(["Never", "Former", "Current"], n, p=[0.50, 0.32, 0.18])
    alcohol_consumption = rng.choice(
        ["None", "Low", "Moderate", "High"], n, p=[0.35, 0.35, 0.22, 0.08]
    )
    physical_activity_level = rng.choice(["Low", "Moderate", "High"], n, p=[0.40, 0.40, 0.20])
    old = age >= 75
    discharge_destination = np.where(
        old,
        rng.choice(
            ["Home", "Home Health Care", "Skilled Nursing Facility", "Rehabilitation", "Other"],
            n,
            p=[0.40, 0.22, 0.22, 0.12, 0.04],
        ),
        rng.choice(
            ["Home", "Home Health Care", "Skilled Nursing Facility", "Rehabilitation", "Other"],
            n,
            p=[0.72, 0.12, 0.05, 0.08, 0.03],
        ),
    )
    follow_up_scheduled = rng.binomial(1, 0.65, n)
    insurance_type = np.where(
        age >= 65,
        rng.choice(["Private", "Medicare", "Medicaid", "Uninsured"], n, p=[0.15, 0.75, 0.08, 0.02]),
        rng.choice(["Private", "Medicare", "Medicaid", "Uninsured"], n, p=[0.58, 0.07, 0.23, 0.12]),
    )

    df = pd.DataFrame(
        {
            "age": age,
            "gender": gender,
            "bmi": bmi,
            "systolic_bp": systolic_bp,
            "diastolic_bp": diastolic_bp,
            "heart_rate": heart_rate,
            "blood_glucose": blood_glucose,
            "hba1c": hba1c,
            "cholesterol": cholesterol,
            "number_of_medications": number_of_medications,
            "previous_admissions": previous_admissions,
            "length_of_stay": length_of_stay,
            "emergency_visits_last_year": emergency_visits_last_year,
            "chronic_disease_count": chronic_disease_count,
            "diabetes": diabetes,
            "hypertension": hypertension,
            "heart_disease": heart_disease,
            "kidney_disease": kidney_disease,
            "smoking_status": smoking_status,
            "alcohol_consumption": alcohol_consumption,
            "physical_activity_level": physical_activity_level,
            "discharge_destination": discharge_destination,
            "follow_up_scheduled": follow_up_scheduled,
            "insurance_type": insurance_type,
            "admission_type": admission_type,
        }
    )

    # --- Target via logistic risk model (computed BEFORE adding noise/missingness) ---
    if include_target:
        abnormal_glucose = ((blood_glucose >= 180) | (hba1c >= 8.0)).astype(float)
        base_logit = (
            0.45 * previous_admissions
            + 0.30 * emergency_visits_last_year
            + 0.22 * chronic_disease_count
            + 0.06 * length_of_stay
            + 0.55 * abnormal_glucose
            - 0.90 * follow_up_scheduled
            + 0.35 * kidney_disease
            + 0.25 * heart_disease
            + 0.012 * (age - 65)
            + 0.45 * np.isin(discharge_destination, ["Skilled Nursing Facility", "Other"])
            + 0.30 * (admission_type == "Emergency")
            + 0.20 * (physical_activity_level == "Low")
            + 0.20 * (smoking_status == "Current")
            + 0.25 * np.isin(insurance_type, ["Medicaid", "Uninsured"])
            + rng.normal(
                0, 0.6, n
            )  # unobserved factors -> keeps the problem realistic, not perfectly separable
        )
        intercept = _solve_intercept(base_logit, positive_rate)
        df[TARGET] = rng.binomial(1, _sigmoid(base_logit + intercept))

    # --- Limited outliers (extreme but physiologically possible) -------------
    for col, (lo, hi) in OUTLIER_SPECS.items():
        idx = rng.choice(n, size=int(round(outlier_rate * n)), replace=False)
        values = rng.uniform(lo, hi, len(idx))
        df[col] = df[col].astype(float)
        df.loc[idx, col] = values.round(1) if col == "bmi" else values.round()

    # --- Missing values ----------------------------------------------------
    for col in MISSING_COLUMNS:
        mask = rng.random(n) < missing_rate
        if df[col].dtype == object or str(df[col].dtype).startswith("str"):
            df[col] = df[col].astype(object)
        else:
            df[col] = df[col].astype(float)
        df.loc[mask, col] = np.nan

    # Store whole-number columns as integers when they have no missing values (cleaner CSV).
    for col in [
        "systolic_bp",
        "diastolic_bp",
        "heart_rate",
        "length_of_stay",
        "blood_glucose",
        "cholesterol",
    ]:
        if df[col].notna().all():
            df[col] = df[col].astype(int)

    df.insert(0, ID_COLUMN, [f"P{i + 1 + id_offset:06d}" for i in range(n)])
    cols = [ID_COLUMN] + FEATURE_COLUMNS + ([TARGET] if include_target else [])
    return df[cols]


# The spec's example request body (used by API docs, tests and smoke checks).
SAMPLE_INPUT = {
    "age": 67,
    "gender": "Male",
    "bmi": 31.5,
    "systolic_bp": 145,
    "diastolic_bp": 92,
    "heart_rate": 88,
    "blood_glucose": 178,
    "hba1c": 8.2,
    "cholesterol": 235,
    "number_of_medications": 7,
    "previous_admissions": 3,
    "length_of_stay": 8,
    "emergency_visits_last_year": 2,
    "chronic_disease_count": 4,
    "diabetes": 1,
    "hypertension": 1,
    "heart_disease": 1,
    "kidney_disease": 0,
    "smoking_status": "Former",
    "alcohol_consumption": "Low",
    "physical_activity_level": "Low",
    "discharge_destination": "Home",
    "follow_up_scheduled": 0,
    "insurance_type": "Private",
    "admission_type": "Emergency",
}


def generate_sample_batch(n_rows: int = 20, seed: int = 2024) -> pd.DataFrame:
    """Small batch file for the /predict/batch demo: new patient IDs, no target, no missing values."""
    return generate_dataset(
        n_rows=n_rows,
        seed=seed,
        missing_rate=0.0,
        outlier_rate=0.0,
        id_offset=900000,
        include_target=False,
    )
