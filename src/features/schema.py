"""Single source of truth for the dataset schema.

Every other module (data generation, validation, training, API, monitoring)
imports column names, allowed categories and plausible numeric ranges from
here, so a schema change only has to be made in ONE place. If you rename a
column here, validation will immediately flag old data as a "schema change".
"""

ID_COLUMN = "patient_id"
TARGET = "readmitted_30_days"

# Continuous / count features (imputed with median, outlier-clipped, scaled).
NUMERIC_FEATURES = [
    "age",
    "bmi",
    "systolic_bp",
    "diastolic_bp",
    "heart_rate",
    "blood_glucose",
    "hba1c",
    "cholesterol",
    "number_of_medications",
    "previous_admissions",
    "length_of_stay",
    "emergency_visits_last_year",
    "chronic_disease_count",
]

# 0/1 flags (imputed with the most frequent value, not scaled).
BINARY_FEATURES = [
    "diabetes",
    "hypertension",
    "heart_disease",
    "kidney_disease",
    "follow_up_scheduled",
]

# Text categories -> one-hot encoded.
CATEGORY_VALUES = {
    "gender": ["Male", "Female", "Other"],
    "smoking_status": ["Never", "Former", "Current"],
    "alcohol_consumption": ["None", "Low", "Moderate", "High"],
    "physical_activity_level": ["Low", "Moderate", "High"],
    "discharge_destination": [
        "Home",
        "Home Health Care",
        "Skilled Nursing Facility",
        "Rehabilitation",
        "Other",
    ],
    "insurance_type": ["Private", "Medicare", "Medicaid", "Uninsured"],
    "admission_type": ["Emergency", "Elective", "Urgent"],
}
CATEGORICAL_FEATURES = list(CATEGORY_VALUES.keys())

# Exact column order of the 25 model inputs (matches the spec's API example).
FEATURE_COLUMNS = [
    "age",
    "gender",
    "bmi",
    "systolic_bp",
    "diastolic_bp",
    "heart_rate",
    "blood_glucose",
    "hba1c",
    "cholesterol",
    "number_of_medications",
    "previous_admissions",
    "length_of_stay",
    "emergency_visits_last_year",
    "chronic_disease_count",
    "diabetes",
    "hypertension",
    "heart_disease",
    "kidney_disease",
    "smoking_status",
    "alcohol_consumption",
    "physical_activity_level",
    "discharge_destination",
    "follow_up_scheduled",
    "insurance_type",
    "admission_type",
]

# Physiologically plausible (min, max) bounds. Values outside these are almost
# certainly data-entry errors, so validation treats them as errors (if many)
# and the API rejects them. Outliers *inside* these bounds are allowed and are
# handled by the outlier-clipping step in the model pipeline.
NUMERIC_RANGES = {
    "age": (18, 110),
    "bmi": (10.0, 80.0),
    "systolic_bp": (60, 260),
    "diastolic_bp": (30, 160),
    "heart_rate": (30, 220),
    "blood_glucose": (40, 600),
    "hba1c": (3.0, 20.0),
    "cholesterol": (80, 500),
    "number_of_medications": (0, 50),
    "previous_admissions": (0, 30),
    "length_of_stay": (1, 90),
    "emergency_visits_last_year": (0, 40),
    "chronic_disease_count": (0, 15),
}

# Sanity check at import time: the three groups must exactly cover FEATURE_COLUMNS.
if sorted(NUMERIC_FEATURES + BINARY_FEATURES + CATEGORICAL_FEATURES) != sorted(FEATURE_COLUMNS):
    raise ValueError("NUMERIC/BINARY/CATEGORICAL feature groups do not match FEATURE_COLUMNS")
