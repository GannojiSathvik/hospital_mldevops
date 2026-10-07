# Shared Contract — Internal Project 2 (Healthcare Readmission DevSecMLOps)

Every module must conform to this. Full requirements: docs/SPEC_original_outline.txt.
Labs covered: **Lab 5 — Secure CI/CD Pipeline (GitHub Actions)**, **Lab 6 — ML Pipeline with Access Control (RBAC)**.

## Environment
- Python 3.11 venv at `.venv/` (already installed — use `.venv/bin/python`, do NOT pip install new packages; if something is truly missing, say so in your report).
- Pinned deps: `requirements-api.txt` (container runtime), `requirements.txt` (full), `requirements-dev.txt` (tooling). Versions: pandas 3.0, numpy 2.4, scikit-learn 1.9, xgboost 3.2, shap 0.51, fastapi 0.142, pydantic 2.13, mlflow 3.16, evidently 0.7.23 (NEW API: `from evidently import Report, Dataset, DataDefinition`; `from evidently.presets import DataDriftPreset`), prefect 3.8.
- All code is run from the repo root; packages `src`, `api`, `pipelines`, `scripts` are importable from repo root (each dir has `__init__.py`). Run scripts as `python -m scripts.train` etc.
- Keep code readable and commented at module/function level — the owner must be able to explain it in a lab viva. Prefer plain, explicit code over clever abstractions.

## Data
- Raw: `data/raw/healthcare_readmission.csv`, >= 5,000 rows, columns: patient_id + the 25 features below + `readmitted_30_days` (0/1, ~15-20% positives). patient_id format `P000001` (synthetic, no PII).
- Feature columns (exact order) — `src/features/schema.py` exports `NUMERIC_FEATURES`, `BINARY_FEATURES`, `CATEGORICAL_FEATURES`, `FEATURE_COLUMNS`, `TARGET`, `ID_COLUMN`, `CATEGORY_VALUES` (dict col -> allowed list), `NUMERIC_RANGES` (dict col -> (min,max)):
  - numeric: age, bmi, systolic_bp, diastolic_bp, heart_rate, blood_glucose, hba1c, cholesterol, number_of_medications, previous_admissions, length_of_stay, emergency_visits_last_year, chronic_disease_count
  - binary (0/1): diabetes, hypertension, heart_disease, kidney_disease, follow_up_scheduled
  - categorical:
    - gender: Male, Female, Other
    - smoking_status: Never, Former, Current
    - alcohol_consumption: None, Low, Moderate, High
    - physical_activity_level: Low, Moderate, High
    - discharge_destination: Home, Home Health Care, Skilled Nursing Facility, Rehabilitation, Other
    - insurance_type: Private, Medicare, Medicaid, Uninsured
    - admission_type: Emergency, Elective, Urgent
- Processed: `data/processed/train.csv`, `data/processed/test.csv` (stratified 80/20, random_state 42).
- `data/sample_input.json` (the spec's example), `data/sample_batch_input.csv` (20 rows incl. patient_id, no target).

## Model artifacts (written by training, read by API & monitoring)
- `models/readmission_model.joblib` — ONE fitted sklearn `Pipeline` (feature engineering + preprocessing + calibrated classifier). Input: pandas DataFrame with the 25 raw FEATURE_COLUMNS (NaN allowed). Use `.predict_proba(df)[:, 1]`.
- `models/preprocessor.joblib` — the fitted preprocessing part alone (for SHAP / inspection).
- `models/threshold.json` — `{"threshold": float, "strategy": str}`
- `models/model_metadata.json` — `{"model_name": str, "model_version": "1.0.0", "trained_at": ISO, "feature_columns": [...], "threshold": float, "metrics": {...}, "training_data_hash": sha256, "trained_by": str}`
- `models/metrics.json` — per-model and best-model metrics.
- `models/feature_list.json` — list of FEATURE_COLUMNS.
- Risk levels (shared helper `src/models/risk.py: risk_level(p: float) -> str`): p < 0.30 -> "Low", p < 0.60 -> "Medium", else "High". prediction = int(p >= threshold); prediction_label = "High Risk of 30-Day Readmission" if 1 else "Low Risk of 30-Day Readmission".

## Lab 6 — Access Control (RBAC) contract
- `configs/access_control.yaml` defines roles -> permissions. Roles & permissions:
  - `viewer`: `model:read`
  - `clinician`: `model:read`, `predict:single`, `predict:explain`
  - `analyst`: `model:read`, `predict:single`, `predict:batch`, `monitor:drift`
  - `ml_engineer`: everything analyst has + `pipeline:train`, `pipeline:validate`, `pipeline:retrain`, `model:register`
  - `admin`: `*`
- `src/security/rbac.py` (owned by API agent) exports:
  - `load_policy(path="configs/access_control.yaml") -> dict`
  - `has_permission(role: str, permission: str, policy=None) -> bool`
  - `resolve_api_key(api_key: str) -> Principal | None` — `Principal(name: str, role: str)` dataclass. API keys come from env var `API_KEYS` formatted `name:role:key,name:role:key`. Keys compared with `hmac.compare_digest`. Only SHA-256 hashes of keys kept in memory.
  - `require_pipeline_permission(permission: str) -> Principal` — for CLI pipelines: reads `PIPELINE_API_KEY` env var, resolves it, raises `PermissionError` if missing/not allowed; logs to audit log.
  - `audit_log(principal_name, role, action, resource, allowed: bool, **extra)` — JSON lines appended to `logs/audit.log`.
- API: header `X-API-Key`. Missing/invalid key -> 401; valid key but lacking permission -> 403. `GET /`, `/health`, `/ready`, `/metrics` are public. `/model-info` needs `model:read`; `/predict` `predict:single`; `/predict/explain` `predict:explain`; `/predict/batch` `predict:batch`; `/monitor/drift` `monitor:drift`.
- Pipelines (`pipelines/training_pipeline.py`, `retraining_pipeline.py`, `validation_pipeline.py`) call `require_pipeline_permission(...)` at start when env `ENFORCE_PIPELINE_RBAC=true` (default true; Makefile/CI set dev keys). `.env.example` provides demo keys for each role.

## Monitoring contract
- API appends every prediction's input features + probability + prediction + timestamp to `data/inference/inference_log.csv` (created if missing).
- `src/monitoring/drift.py` exports `run_drift_report(reference_df, current_df, output_dir="reports/drift") -> dict` returning `{"dataset_drift_share": float, "drifted_columns": [...], "n_drifted": int, "n_columns": int, "drift_detected": bool, "report_path": str}`; writes HTML + JSON to reports/drift/.
- `src/monitoring/retrain_policy.py`: `should_retrain(drift_share, current_roc_auc=None, prediction_shift=None, config=None) -> (bool, list[str] reasons)`, thresholds from `configs/monitoring_config.yaml` (drift_share > 0.30, roc_auc < 0.70, positive-rate shift > 0.10).
- Prometheus metric names (API): `predictions_total{endpoint,risk_level}`, `prediction_latency_seconds` (histogram), `http_requests_total{method,path,status}`, `api_errors_total`, `high_risk_predictions_total`, `auth_failures_total{reason}`.

## Ports / services
- API: 8000. MLflow: 5000 (tracking URI env `MLFLOW_TRACKING_URI`, default `file:./mlruns`... use `sqlite:///mlflow.db` locally). Prometheus: 9090. Grafana: 3000.
- Docker image name: `healthcare-readmission-api`. Container runs as non-root user `appuser` (uid 10001).
