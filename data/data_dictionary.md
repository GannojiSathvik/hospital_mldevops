# Data Dictionary — `data/raw/healthcare_readmission.csv`

> **Synthetic data, educational use only.** Every row is simulated by
> `src/data/generate.py` (seed 42). No real patients, names, addresses or record
> numbers. Not for clinical decision-making.

- Rows: 6,000 (configurable in `params.yaml` → `data.n_rows`)
- Target prevalence: ~17% readmitted (class imbalance by design)
- Missing values: ~4% in `bmi`, `blood_glucose`, `hba1c`, `cholesterol`, `smoking_status`, `alcohol_consumption`
- Outliers: ~0.5% extreme-but-plausible values in `bmi`, `blood_glucose`, `cholesterol`, `length_of_stay`
- Schema source of truth: `src/features/schema.py`; validation rules: `configs/validation_config.yaml`
- **CSV reading note:** `"None"` is a valid `alcohol_consumption` value; pandas treats it as NaN by default.
  Read with `src.data.preprocess.read_patient_csv()` (`keep_default_na=False, na_values=[""...]`).

| Column | Type | Allowed values / range | Description | Effect on readmission risk (simulated) |
|---|---|---|---|---|
| patient_id | string | `P000001`… | Synthetic identifier (must be unique) | none — not a model input |
| age | int | 18–110 | Age in years | small ↑ with age |
| gender | category | Male, Female, Other | Gender | none |
| bmi | float | 10–80 | Body-mass index (kg/m²) | indirect (via diabetes/hypertension) |
| systolic_bp | int | 60–260 | Systolic blood pressure (mmHg) | indirect |
| diastolic_bp | int | 30–160 | Diastolic blood pressure (mmHg) | indirect |
| heart_rate | int | 30–220 | Resting heart rate (bpm) | none |
| blood_glucose | float | 40–600 | Blood glucose (mg/dL) | ↑ if ≥ 180 (abnormal) |
| hba1c | float | 3–20 | HbA1c (%) — 3-month glucose control | ↑ if ≥ 8.0 |
| cholesterol | float | 80–500 | Total cholesterol (mg/dL) | none |
| number_of_medications | int | 0–50 | Active medications at discharge | indirect (via chronic disease) |
| previous_admissions | int | 0–30 | Prior inpatient admissions | strong ↑ |
| length_of_stay | int | 1–90 | Days in hospital this admission | ↑ |
| emergency_visits_last_year | int | 0–40 | ED visits in past 12 months | ↑ |
| chronic_disease_count | int | 0–15 | Number of chronic conditions | ↑ |
| diabetes | 0/1 | 0, 1 | Diabetes diagnosis | indirect (glucose) |
| hypertension | 0/1 | 0, 1 | Hypertension diagnosis | indirect |
| heart_disease | 0/1 | 0, 1 | Heart disease diagnosis | ↑ |
| kidney_disease | 0/1 | 0, 1 | Chronic kidney disease | ↑ |
| smoking_status | category | Never, Former, Current | Smoking history | ↑ if Current |
| alcohol_consumption | category | None, Low, Moderate, High | Alcohol use | none |
| physical_activity_level | category | Low, Moderate, High | Activity level | ↑ if Low |
| discharge_destination | category | Home, Home Health Care, Skilled Nursing Facility, Rehabilitation, Other | Where the patient went after discharge | ↑ if SNF / Other |
| follow_up_scheduled | 0/1 | 0, 1 | Follow-up appointment booked at discharge | strong ↓ (protective) |
| insurance_type | category | Private, Medicare, Medicaid, Uninsured | Payer | ↑ if Medicaid / Uninsured |
| admission_type | category | Emergency, Elective, Urgent | How the patient was admitted | ↑ if Emergency |
| **readmitted_30_days** | 0/1 | 0, 1 | **Target:** readmitted within 30 days of discharge | — |

## Engineered features (added inside the model pipeline by `FeatureEngineer`)

| Feature | Definition | Rationale |
|---|---|---|
| pulse_pressure | systolic_bp − diastolic_bp | Arterial stiffness / cardiovascular risk proxy |
| bp_high | systolic ≥ 140 or diastolic ≥ 90 | Stage-2 hypertension flag |
| glucose_abnormal | glucose ≥ 180 or < 70, or HbA1c ≥ 6.5 | Poor glycaemic control |
| utilization_score | previous_admissions + emergency_visits_last_year | Overall healthcare utilisation |
| polypharmacy | number_of_medications ≥ 5 | Common clinical polypharmacy definition |
