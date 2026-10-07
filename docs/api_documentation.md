# API Documentation

> Educational project on synthetic data. Not for clinical use.

Base URL (local): `http://127.0.0.1:8000` (`make run-api`). Interactive
OpenAPI docs: `/docs` and `/redoc` (disabled when `ENV=production`).

## Authentication and authorization

Send the key in the `X-API-Key` header. Keys are configured in the `API_KEYS`
env var (`name:role:key,...`). Missing/invalid key -> **401**; valid key whose
role lacks the permission -> **403**. Full explanation: `docs/access_control.md`.

| Method | Path | Permission | Roles allowed | Rate limited |
|--------|------|-----------|---------------|:---:|
| GET | `/` | public | everyone | no |
| GET | `/health` | public | everyone | no |
| GET | `/ready` | public | everyone | no |
| GET | `/metrics` | public | everyone | no |
| GET | `/model-info` | `model:read` | viewer, clinician, analyst, ml_engineer, admin | no |
| POST | `/predict` | `predict:single` | clinician, analyst, ml_engineer, admin | yes |
| POST | `/predict/explain` | `predict:explain` | clinician, admin | yes |
| POST | `/predict/batch` | `predict:batch` | analyst, ml_engineer, admin | yes |
| POST | `/monitor/drift` | `monitor:drift` | analyst, ml_engineer, admin | no |

Rate limit: `RATE_LIMIT` env var, default `60/minute`, counted per API key
(hashed) or per client IP when no key is sent. Exceeding it returns 429.

## Common response headers

Every response carries `X-Request-ID` (echoes a safe client-supplied value or a
new UUID) and the secure headers `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Content-Security-Policy`, `Strict-Transport-Security`, `Cache-Control: no-store`.

## Endpoints

### GET /

```json
{"service":"Healthcare Readmission Risk API","version":"1.0.0","disclaimer":"Educational/demo project on synthetic data. Not for clinical diagnosis or treatment decisions without professional validation.","docs":"/docs","auth":"Send your API key in the X-API-Key header."}
```

### GET /health — liveness

Process is up; does not check the model.

```json
{"status":"ok","timestamp":"2026-10-06T11:12:44.829380+00:00"}
```

### GET /ready — readiness

200 `{"status":"ready","model_loaded":true,"model_version":"1.0.0"}` once
artifacts are loaded; 503 `{"status":"not ready","model_loaded":false}` otherwise.

### GET /metrics

Prometheus text format: `predictions_total{endpoint,risk_level}`,
`prediction_latency_seconds` (histogram), `http_requests_total{method,path,status}`,
`api_errors_total`, `high_risk_predictions_total`, `auth_failures_total{reason}`.

### GET /model-info

```json
{"model_name":"CalibratedLogisticRegression","model_version":"1.0.0","trained_at":"2026-10-06T11:07:57.555557+00:00","threshold":0.13272914824638962,"threshold_strategy":"max_precision_at_recall>=0.78","feature_columns":["age","gender","bmi","..."],"metrics":{"accuracy":0.6392,"precision":0.2943,"recall":0.774,"f1":0.4265,"roc_auc":0.7648,"pr_auc":0.454,"threshold":0.1327,"confusion_matrix":{"tn":606,"fp":386,"fn":47,"tp":161}},"training_data_hash":"d3678a4a7ee074299c8b3de74c742c7067130864e837f0fb61a4ec16669c9dfd"}
```

### POST /predict

Request body: the 25 features (`data/sample_input.json`). Validation
(`api/schemas.py`): numeric fields within `NUMERIC_RANGES` (e.g. age 18-110),
binary fields exactly 0 or 1, categorical fields one of the allowed values,
**unknown extra fields rejected**. No `patient_id` is accepted on this endpoint.

```bash
curl -s -X POST http://127.0.0.1:8000/predict \
  -H "X-API-Key: demo-clinician-key-change-me" \
  -H "Content-Type: application/json" -d @data/sample_input.json
```

```json
{"prediction":1,"prediction_label":"High Risk of 30-Day Readmission","readmission_probability":0.7793,"risk_level":"High","model_version":"1.0.0","timestamp":"2026-10-06T11:12:44.951272+00:00"}
```

- `prediction` = 1 if `readmission_probability >= 0.1327` (the tuned threshold in `models/threshold.json`).
- `risk_level`: Low (p < threshold), Medium (threshold <= p < 0.60), High (p >= 0.60).

### POST /predict/explain

Same request as `/predict`. Response adds:

- `top_features`: the 10 largest SHAP contributions, each `{feature, value, contribution}`. `value` is the transformed (scaled/encoded) feature value; `contribution` is in probability units (positive pushes risk up).
- `explanation_method`: `"shap-permutation (probability units)"`.

Observed response (truncated):

```json
{"prediction":1,"readmission_probability":0.7793,"risk_level":"High","top_features":[{"feature":"bin__follow_up_scheduled","value":0.0,"contribution":0.1214},{"feature":"num__utilization_score","value":1.6312,"contribution":0.1085},{"feature":"num__chronic_disease_count","value":1.6916,"contribution":0.0883}],"explanation_method":"shap-permutation (probability units)"}
```

Feature names are the preprocessed column names: the prefix says which branch
of the ColumnTransformer produced them (`num__` numeric, `bin__` binary, `cat__`
one-hot category, e.g. `cat__insurance_type_Private`). `value` is the
transformed (scaled / encoded) value. The first call is slow (about 3 s)
because the SHAP explainer is built lazily.

### POST /predict/batch

`multipart/form-data` with field `file`: a `.csv` file whose content type is
`text/csv`, `application/csv`, `application/vnd.ms-excel` or `text/plain`,
containing `patient_id` + all 25 feature columns (`data/sample_batch_input.csv`).

```bash
curl -s -X POST http://127.0.0.1:8000/predict/batch \
  -H "X-API-Key: demo-analyst-key-change-me" \
  -F "file=@data/sample_batch_input.csv;type=text/csv" -o predictions.csv
```

Response: `text/csv` attachment `predictions.csv`:

```
patient_id,prediction,prediction_label,readmission_probability,risk_level,model_version,prediction_timestamp
P900001,1,High Risk of 30-Day Readmission,0.1828,Medium,1.0.0,2026-10-06T11:12:44.987296+00:00
P900004,0,Low Risk of 30-Day Readmission,0.0615,Low,1.0.0,2026-10-06T11:12:44.987296+00:00
```

Upload safety checks, in order: extension + content type (400), size above
`MAX_UPLOAD_MB` (default 5) -> 413 (the server reads at most limit + 1 bytes),
unparseable CSV (400), missing columns / empty file (400), more than
`MAX_BATCH_ROWS` (default 10,000) rows (413), out-of-range numbers or unknown
categories (400 with up to 20 reasons).

### POST /monitor/drift

No body. Compares `data/inference/inference_log.csv` (needs >= 30 rows,
otherwise 400) with `data/processed/train.csv` using Evidently and writes an
HTML + JSON report to `reports/drift/`.

```json
{"dataset_drift_share":0.0,"drifted_columns":[],"n_drifted":0,"n_columns":25,"drift_detected":false,"report_path":"reports/drift/drift_report_<timestamp>.html","n_reference_rows":4800,"n_current_rows":500}
```

### GET /schema/features — public input contract
Returns the numeric ranges, binary columns, allowed categories and feature order
from `src/features/schema.py`. The web UI builds its form from this, so the
browser enforces the same limits the server validates (the server still
re-validates every request).

### GET /whoami — any valid key
Authentication only (no permission needed): returns the caller's `name`, `role`,
role `description` and `permissions`. Missing/invalid key -> 401. The web UI uses
it to show what the role may do; it never replaces server-side checks.

```json
{"name":"demo-clinician","role":"clinician","description":"Single-patient scoring and explanations at the point of care.","permissions":["model:read","predict:single","predict:explain"]}
```

### GET /ui/ — web UI
Static HTML/CSS/JS from `ui/`, served with a strict Content-Security-Policy
(`script-src 'self'`, `connect-src 'self'`, no inline code). `/ui` redirects to `/ui/`.

## Error format

| Status | When | Body |
|--------|------|------|
| 400 | Bad upload / bad CSV content / not enough drift data | `{"detail": "..."}` |
| 401 | Missing or invalid key | `{"detail":"Missing API key"}` / `{"detail":"Invalid API key"}` + `WWW-Authenticate: ApiKey` |
| 403 | Role lacks permission | `{"detail":"Role 'viewer' lacks permission 'predict:single'"}` |
| 413 | File or row count too large | `{"detail":"File too large (max 5 MB)"}` |
| 422 | Body validation failed | `{"detail":[{"loc":["body","age"],"msg":"Input should be less than or equal to 110","type":"less_than_equal"}],"request_id":"..."}` (submitted values are not echoed) |
| 429 | Rate limit | `{"detail":"Rate limit exceeded","request_id":"..."}` |
| 500 | Unexpected error | `{"detail":"Internal server error","request_id":"..."}` (stack trace only in server log) |
| 503 | Model not loaded | `{"detail":"Model not available","request_id":"..."}` |
