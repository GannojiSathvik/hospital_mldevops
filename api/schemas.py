"""Pydantic request/response models (input validation + API documentation).

Why: the API must never pass garbage to the model. Pydantic validates every
incoming JSON body *before* our code runs:

* numeric fields must lie inside the plausible ranges from
  ``src/features/schema.py`` (``NUMERIC_RANGES``) - e.g. age 18..110;
* binary flags must be exactly 0 or 1;
* categorical fields must be one of the allowed values (``CATEGORY_VALUES``),
  expressed as ``Literal[...]`` types so they also show up in /docs;
* unknown extra fields are rejected (``extra="forbid"``) to catch typos and
  stop clients smuggling unexpected data in.

Invalid input -> FastAPI returns 422 with the field name and reason only.
The ranges/categories are imported from the shared schema module so the API,
data validation and training can never disagree.
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, get_args

from pydantic import BaseModel, ConfigDict, Field

from src.features.schema import CATEGORY_VALUES, NUMERIC_RANGES

# Literal types for the categorical fields. mypy needs these spelled out statically
# (a Literal built from a runtime list is not a valid type), so we write them out here
# and then *verify at import time* that they match the shared CATEGORY_VALUES exactly -
# the shared schema module therefore stays the single source of truth.
Gender: TypeAlias = Literal["Male", "Female", "Other"]
Smoking: TypeAlias = Literal["Never", "Former", "Current"]
Alcohol: TypeAlias = Literal["None", "Low", "Moderate", "High"]
Activity: TypeAlias = Literal["Low", "Moderate", "High"]
Discharge: TypeAlias = Literal[
    "Home", "Home Health Care", "Skilled Nursing Facility", "Rehabilitation", "Other"
]
Insurance: TypeAlias = Literal["Private", "Medicare", "Medicaid", "Uninsured"]
Admission: TypeAlias = Literal["Emergency", "Elective", "Urgent"]
Binary: TypeAlias = Literal[0, 1]

_LITERAL_FOR_COLUMN = {
    "gender": Gender,
    "smoking_status": Smoking,
    "alcohol_consumption": Alcohol,
    "physical_activity_level": Activity,
    "discharge_destination": Discharge,
    "insurance_type": Insurance,
    "admission_type": Admission,
}
for _col, _lit in _LITERAL_FOR_COLUMN.items():
    if list(get_args(_lit)) != list(CATEGORY_VALUES[_col]):
        raise ValueError(f"api.schemas Literal for {_col!r} is out of sync with CATEGORY_VALUES")


def _rng(col: str, description: str) -> Any:
    """A required pydantic Field enforcing the shared min/max for a numeric column."""
    lo, hi = NUMERIC_RANGES[col]
    return Field(..., ge=lo, le=hi, description=f"{description} ({lo}-{hi})")


class PatientFeatures(BaseModel):
    """One patient's 25 model inputs (no identifiers / PII)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
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
        },
    )

    age: float = _rng("age", "Age in years")
    gender: Gender
    bmi: float = _rng("bmi", "Body-mass index")
    systolic_bp: float = _rng("systolic_bp", "Systolic BP mmHg")
    diastolic_bp: float = _rng("diastolic_bp", "Diastolic BP mmHg")
    heart_rate: float = _rng("heart_rate", "Heart rate bpm")
    blood_glucose: float = _rng("blood_glucose", "Blood glucose mg/dL")
    hba1c: float = _rng("hba1c", "HbA1c %")
    cholesterol: float = _rng("cholesterol", "Total cholesterol mg/dL")
    number_of_medications: int = _rng("number_of_medications", "Active medications")
    previous_admissions: int = _rng("previous_admissions", "Prior admissions")
    length_of_stay: float = _rng("length_of_stay", "Length of stay, days")
    emergency_visits_last_year: int = _rng(
        "emergency_visits_last_year", "ER visits in last 12 months"
    )
    chronic_disease_count: int = _rng("chronic_disease_count", "Chronic conditions")
    diabetes: Binary
    hypertension: Binary
    heart_disease: Binary
    kidney_disease: Binary
    smoking_status: Smoking
    alcohol_consumption: Alcohol
    physical_activity_level: Activity
    discharge_destination: Discharge
    follow_up_scheduled: Binary
    insurance_type: Insurance
    admission_type: Admission


class PredictionResponse(BaseModel):
    """Exact single-prediction output shape required by the spec."""

    prediction: Literal[0, 1]
    prediction_label: str
    readmission_probability: float = Field(..., ge=0.0, le=1.0)
    risk_level: Literal["Low", "Medium", "High"]
    model_version: str
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp")


class FeatureContribution(BaseModel):
    feature: str
    value: float | str | None = Field(None, description="Transformed feature value")
    contribution: float = Field(..., description="SHAP value (log-odds / margin space)")


class ExplanationResponse(PredictionResponse):
    """Prediction plus the top-10 features that pushed the risk up or down."""

    top_features: list[FeatureContribution]
    explanation_method: str = "shap"


class HealthResponse(BaseModel):
    status: str
    timestamp: str


class ReadyResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str | None = None


class ModelInfoResponse(BaseModel):
    model_name: str | None = None
    model_version: str | None = None
    trained_at: str | None = None
    threshold: float | None = None
    threshold_strategy: str | None = None
    feature_columns: list[str] = []
    metrics: dict = {}
    training_data_hash: str | None = None


class DriftResponse(BaseModel):
    dataset_drift_share: float
    drifted_columns: list[str]
    n_drifted: int
    n_columns: int
    drift_detected: bool
    report_path: str
    n_reference_rows: int | None = None
    n_current_rows: int | None = None
