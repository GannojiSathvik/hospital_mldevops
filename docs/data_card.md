# Data Card — Synthetic Healthcare Readmission Dataset

> **Synthetic data, educational use only.** No real patients, names,
> addresses, phone numbers, email addresses or medical record numbers.

Column-level details (types, ranges, simulated effects): `data/data_dictionary.md`.

## Summary

| Item | Value |
|------|-------|
| File | `data/raw/healthcare_readmission.csv` |
| Generator | `src/data/generate.py` via `make generate-data` (`python -m scripts.generate_dataset`) |
| Seed / size | 42 / 6,000 rows (`params.yaml` -> `data`) |
| Columns | `patient_id` (`P000001` format) + 25 features + `readmitted_30_days` |
| Target prevalence | 17.35% (1,041 readmitted) — configured `positive_rate: 0.17` |
| Split | Stratified 80/20, `random_state=42`: train 4,800 rows (17.35% positive), test 1,200 rows (17.33% positive) |
| Versioning | DVC (`dvc.yaml`, `params.yaml`); training-data SHA-256 stored in model metadata |

## How it was generated

1. Patient characteristics are drawn with plausible correlations (older patients have more hypertension, diabetics have higher glucose/HbA1c, more chronic diseases mean more medications and admissions).
2. Readmission probability is a logistic function of clinically motivated factors, e.g. `+0.45*previous_admissions +0.30*emergency_visits +0.22*chronic_disease_count +0.06*length_of_stay +0.55*abnormal_glucose -0.90*follow_up_scheduled ...`.
3. The intercept is solved by bisection so about 17% of patients are positive; each label is drawn from a Bernoulli distribution with that probability.
4. Missing values (configured `missing_rate: 0.04`) and outliers (`outlier_rate: 0.005`) are injected.

## Data quality characteristics (raw file)

| Issue | Columns | Count |
|-------|---------|-------|
| Missing | bmi 242, blood_glucose 239, hba1c 263, cholesterol 227, smoking_status 227, alcohol_consumption 228 | about 4% each |
| Outliers | bmi, blood_glucose, cholesterol, length_of_stay | about 0.5% |

**Important:** `"None"` is a valid `alcohol_consumption` category (2,031 rows).
Default `pd.read_csv` converts it to NaN (it would appear as ~2,259 missing).
All project code reads patient CSVs with `read_patient_csv()`
(`keep_default_na=False`).

## Validation

`pipelines/validation_pipeline.py` with `configs/validation_config.yaml`
checks: required columns, data types, allowed categories, missing fraction per
column (> 0.20 error, > 0.05 warning), numeric ranges, duplicate patient IDs,
unexpected columns (schema change warning), target values {0,1}, positive rate
between 0.02 and 0.60, imbalance warning below 0.20, and leakage (any feature
with |correlation| > 0.95 to the target, or a suspicious name such as
`readmi`, `target`, `label`, `outcome`). Report:
`reports/evaluation/validation_report.json`.

## Other data files

| File | Content |
|------|---------|
| `data/sample_input.json` | One patient, the spec's example |
| `data/sample_batch_input.csv` | 20 patients (`P900001`...) with `patient_id`, no target |
| `data/inference/inference_log.csv` | Inputs + outputs of every API prediction (or seeded by `scripts/seed_monitoring_data.py`) for drift monitoring |
| `data/inference/labeled_feedback.csv` | Optional simulated true outcomes (created with `--with-labels`) |

## Limitations

- Relationships are hand-designed; they reflect general clinical intuition, not measured effects.
- No temporal structure, no hospital/site effects, no text or codes (ICD), no social determinants beyond insurance type.
- Not suitable for estimating real-world performance or for fairness conclusions.

## Privacy

`patient_id` values are sequential synthetic identifiers and are not model
inputs. The single-prediction API does not accept any identifier.
