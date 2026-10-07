"""FastAPI application: Healthcare 30-day readmission risk prediction API.

EDUCATIONAL / DEMO ONLY - trained on synthetic data; not for clinical use.

Endpoints (permission in brackets; see configs/access_control.yaml):
  GET  /                 public    - service info + disclaimer
  GET  /health           public    - liveness: process is up
  GET  /ready            public    - readiness: model loaded? (503 if not)
  GET  /metrics          public    - Prometheus metrics
  GET  /model-info       [model:read]
  POST /predict          [predict:single]   - one patient (JSON)
  POST /predict/explain  [predict:explain]  - one patient + SHAP top-10
  POST /predict/batch    [predict:batch]    - CSV upload -> CSV download
  POST /monitor/drift    [monitor:drift]    - Evidently drift of inference log vs training data

Security features wired in here:
* API-key auth + RBAC via ``Depends(require_permission(...))`` (401 vs 403).
* Rate limiting with slowapi on /predict* (env ``RATE_LIMIT``, default
  60/minute) keyed by API key (hashed) or client IP -> 429 when exceeded.
* Safe uploads: only ``.csv`` with a CSV content type, max ``MAX_UPLOAD_MB``
  (default 5 MB) -> 413, max ``MAX_BATCH_ROWS`` rows (default 10,000) -> 413,
  required columns + allowed values checked -> 400.
* Errors never leak internals: validation errors return 422 with field
  location/message only; unexpected errors return a generic 500 with the
  request ID (see api/middleware.py).
* Interactive docs (/docs, /redoc, /openapi.json) are enabled for development
  and disabled when ``ENV=production`` to reduce the attack surface
  (no free map of the API for attackers).
"""

from __future__ import annotations

import hashlib
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.logging_config import get_logger, setup_logging
from api.middleware import RequestContextMiddleware
from api.prediction_service import ModelNotLoadedError, service
from api.schemas import (
    DriftResponse,
    ExplanationResponse,
    HealthResponse,
    ModelInfoResponse,
    PatientFeatures,
    PredictionResponse,
    ReadyResponse,
)
from api.security import require_permission
from src.data.preprocess import READ_CSV_KWARGS, read_patient_csv
from src.features.schema import (
    BINARY_FEATURES,
    CATEGORICAL_FEATURES,
    CATEGORY_VALUES,
    FEATURE_COLUMNS,
    ID_COLUMN,
    NUMERIC_FEATURES,
    NUMERIC_RANGES,
)
from src.security.rbac import load_policy

setup_logging()
logger = get_logger("api")

ENV = os.getenv("ENV", "development").lower()
APP_VERSION = "1.0.0"
DISCLAIMER = (
    "Educational/demo project on synthetic data. Not for clinical diagnosis or "
    "treatment decisions without professional validation."
)
INFERENCE_LOG = Path(os.getenv("INFERENCE_LOG_PATH", "data/inference/inference_log.csv"))
REFERENCE_DATA = Path("data/processed/train.csv")
MIN_DRIFT_ROWS = 30
BATCH_COLUMNS = [
    "patient_id",
    "prediction",
    "prediction_label",
    "readmission_probability",
    "risk_level",
    "model_version",
    "prediction_timestamp",
]
ALLOWED_CSV_TYPES = {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}


def _max_upload_bytes() -> int:
    return int(float(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024)


def _max_rows() -> int:
    return int(os.getenv("MAX_BATCH_ROWS", "10000"))


# ---------------------------------------------------------------- rate limit
def _rate_limit_key(request: Request) -> str:
    """Limit per API key (hashed, so the raw key is never kept) else per IP."""
    key = request.headers.get("X-API-Key")
    if key:
        return "key:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    return "ip:" + get_remote_address(request)


def _rate_limit() -> str:
    return os.getenv("RATE_LIMIT", "60/minute")


limiter = Limiter(key_func=_rate_limit_key)


# ------------------------------------------------------------------ app setup
@asynccontextmanager
async def lifespan(_: FastAPI):
    # Try to load the model at startup; failure is reported by /ready, not fatal.
    if not service.load():
        logger.warning("Model not loaded at startup", extra={"error": service.load_error})
    yield


app = FastAPI(
    title="Healthcare Readmission Risk API",
    version=APP_VERSION,
    description="Predicts 30-day readmission risk. " + DISCLAIMER,
    lifespan=lifespan,
    docs_url=None if ENV == "production" else "/docs",
    redoc_url=None if ENV == "production" else "/redoc",
    openapi_url=None if ENV == "production" else "/openapi.json",
)
app.state.limiter = limiter
app.add_middleware(RequestContextMiddleware)

# Browser UI: plain HTML/CSS/JS in ui/, served from the same origin as the API
# (so no CORS is needed and the strict UI Content-Security-Policy applies).
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
if UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Only field location + message + type; we do not echo submitted values.
    errors = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": errors, "request_id": getattr(request.state, "request_id", None)},
    )


@app.exception_handler(ModelNotLoadedError)
async def model_not_loaded_handler(request: Request, exc: ModelNotLoadedError):
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Model not available",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------------------------------------------------------------- public routes
@app.get("/", tags=["info"])
def root() -> dict:
    return {
        "service": "Healthcare Readmission Risk API",
        "version": APP_VERSION,
        "disclaimer": DISCLAIMER,
        "docs": None if ENV == "production" else "/docs",
        "auth": "Send your API key in the X-API-Key header.",
    }


@app.get("/ui", include_in_schema=False)
def ui_redirect() -> RedirectResponse:
    """/ui -> /ui/ so relative paths to app.js and styles.css resolve."""
    return RedirectResponse("/ui/")


@app.get("/schema/features", tags=["info"])
def feature_schema() -> dict:
    """Public input contract (ranges + categories) so the UI form uses the
    same limits the server validates against - one source of truth."""
    return {
        "numeric": {c: list(NUMERIC_RANGES[c]) for c in NUMERIC_FEATURES},
        "binary": list(BINARY_FEATURES),
        "categorical": {c: list(CATEGORY_VALUES[c]) for c in CATEGORICAL_FEATURES},
        "feature_order": list(FEATURE_COLUMNS),
    }


@app.get("/whoami", tags=["auth"])
def whoami(principal=Depends(require_permission(None))) -> dict:
    """Any valid key: returns the caller's name, role and granted permissions.

    The UI uses this to show which actions the role may perform. It is only a
    convenience - every endpoint still enforces its own permission server-side.
    """
    spec = load_policy().get("roles", {}).get(principal.role, {})
    return {
        "name": principal.name,
        "role": principal.role,
        "description": spec.get("description", ""),
        "permissions": list(spec.get("permissions", [])),
    }


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> dict:
    """Liveness probe: the process is running (does not check the model)."""
    return {"status": "ok", "timestamp": _now()}


@app.get(
    "/ready",
    response_model=ReadyResponse,
    tags=["ops"],
    responses={503: {"description": "Model not loaded"}},
)
def ready():
    """Readiness probe: only 'ready' once the model artifacts are loaded."""
    if not service.load():
        return JSONResponse(status_code=503, content={"status": "not ready", "model_loaded": False})
    return {"status": "ready", "model_loaded": True, "model_version": service.model_version}


@app.get("/metrics", tags=["ops"])
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ----------------------------------------------------------- protected routes
@app.get("/model-info", response_model=ModelInfoResponse, tags=["model"])
def model_info(_principal=Depends(require_permission("model:read"))) -> dict:
    if not service.load():
        raise ModelNotLoadedError()
    m = service.metadata
    return {
        "model_name": m.get("model_name"),
        "model_version": m.get("model_version"),
        "trained_at": m.get("trained_at"),
        "threshold": service.threshold,
        "threshold_strategy": service.threshold_strategy,
        "feature_columns": m.get("feature_columns", FEATURE_COLUMNS),
        "metrics": m.get("metrics", {}),
        "training_data_hash": m.get("training_data_hash"),
    }


@app.post("/predict", response_model=PredictionResponse, tags=["predict"])
@limiter.limit(_rate_limit)
def predict(
    request: Request,
    patient: PatientFeatures,
    _principal=Depends(require_permission("predict:single")),
) -> dict:
    return service.predict_one(patient.model_dump())


@app.post("/predict/explain", response_model=ExplanationResponse, tags=["predict"])
@limiter.limit(_rate_limit)
def predict_explain(
    request: Request,
    patient: PatientFeatures,
    _principal=Depends(require_permission("predict:explain")),
) -> dict:
    return service.explain(patient.model_dump())


def _validate_batch_frame(df: pd.DataFrame) -> None:
    """Check required columns and allowed values; raise 400 with a short reason."""
    required = [ID_COLUMN] + FEATURE_COLUMNS
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise HTTPException(400, f"Missing required columns: {missing}")
    if df.empty:
        raise HTTPException(400, "CSV contains no rows")
    if len(df) > _max_rows():
        raise HTTPException(413, f"Too many rows (max {_max_rows()})")
    problems: list[str] = []
    for col, (lo, hi) in NUMERIC_RANGES.items():
        values = pd.to_numeric(df[col], errors="coerce")
        if (values.isna() & df[col].notna()).any():
            problems.append(f"{col}: non-numeric values")
        elif ((values < lo) | (values > hi)).any():
            problems.append(f"{col}: values outside {lo}-{hi}")
    for col, allowed in CATEGORY_VALUES.items():
        bad = df[col].dropna()
        if (~bad.isin(allowed)).any():
            problems.append(f"{col}: values not in {allowed}")
    if problems:
        raise HTTPException(400, {"message": "Invalid CSV content", "errors": problems[:20]})


@app.post(
    "/predict/batch",
    tags=["predict"],
    responses={200: {"content": {"text/csv": {}}}, 400: {}, 413: {}},
)
@limiter.limit(_rate_limit)
async def predict_batch(
    request: Request,
    file: UploadFile = File(..., description="CSV with patient_id + 25 feature columns"),
    _principal=Depends(require_permission("predict:batch")),
):
    # 1) File type: extension AND declared content type must look like CSV.
    filename = (file.filename or "").lower()
    if not filename.endswith(".csv") or (file.content_type or "") not in ALLOWED_CSV_TYPES:
        raise HTTPException(400, "Only .csv files (text/csv) are accepted")

    # 2) Size: read at most limit+1 bytes so a huge upload can't exhaust memory.
    limit = _max_upload_bytes()
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"File too large (max {limit // (1024 * 1024)} MB)")

    # 3) Parse + validate. dtype=str for patient_id keeps leading zeros intact.
    try:
        df = pd.read_csv(io.BytesIO(content), dtype={ID_COLUMN: str}, **READ_CSV_KWARGS)
    except Exception:  # noqa: BLE001 - malformed CSV is a client error
        raise HTTPException(400, "Could not parse CSV file") from None
    _validate_batch_frame(df)

    # 4) Predict and stream back a CSV with exactly the contract columns.
    results = service.predict_batch(df)
    out = pd.DataFrame(results).rename(columns={"timestamp": "prediction_timestamp"})
    out.insert(0, "patient_id", df[ID_COLUMN].astype(str).values)
    buffer = io.StringIO()
    out[BATCH_COLUMNS].to_csv(buffer, index=False)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="predictions.csv"'},
    )


@app.post("/monitor/drift", response_model=DriftResponse, tags=["monitoring"])
def monitor_drift(_principal=Depends(require_permission("monitor:drift"))) -> dict:
    """Compare recent inference inputs with training data using Evidently."""
    if not INFERENCE_LOG.exists():
        raise HTTPException(400, f"Not enough inference data (need >= {MIN_DRIFT_ROWS} rows)")
    current = read_patient_csv(INFERENCE_LOG)
    if len(current) < MIN_DRIFT_ROWS:
        raise HTTPException(400, f"Not enough inference data (need >= {MIN_DRIFT_ROWS} rows)")
    if not REFERENCE_DATA.exists():
        raise HTTPException(503, "Reference (training) data not available")
    try:
        from src.monitoring.drift import run_drift_report  # lazy: Evidently is heavy
    except ImportError:
        raise HTTPException(503, "Drift monitoring module not available") from None
    reference = read_patient_csv(REFERENCE_DATA)
    report = run_drift_report(reference, current)
    report = dict(report)
    report.setdefault("n_reference_rows", len(reference))
    report.setdefault("n_current_rows", len(current))
    return report
