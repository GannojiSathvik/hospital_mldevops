"""Shared pytest fixtures.

* Sets demo API keys (one per role) in ``API_KEYS`` *before* the app is
  imported, so tests exercise the real authentication path.
* Redirects the audit log and inference log to a temporary directory so
  tests never pollute the real ``logs/`` or ``data/inference/`` files.
* Disables pipeline RBAC enforcement (``ENFORCE_PIPELINE_RBAC=false``) so the
  other test suites can run pipelines without a key.
* API tests use the real trained artifacts in ``models/``; if they are not
  there yet (run ``make train`` first), those tests are skipped with a reason.

These keys are throwaway test values, not secrets.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

ROLE_KEYS = {
    "viewer": "test-viewer-key-123",
    "clinician": "test-clinician-key-123",
    "analyst": "test-analyst-key-123",
    "ml_engineer": "test-mlengineer-key-123",
    "admin": "test-admin-key-123",
}

_TMP = Path(tempfile.mkdtemp(prefix="readmission-tests-"))
os.environ["API_KEYS"] = ",".join(f"{role}-user:{role}:{key}" for role, key in ROLE_KEYS.items())
os.environ["ENFORCE_PIPELINE_RBAC"] = "false"
os.environ["AUDIT_LOG_PATH"] = str(_TMP / "audit.log")
os.environ["INFERENCE_LOG_PATH"] = str(_TMP / "inference_log.csv")
os.environ["RATE_LIMIT"] = "10000/minute"
os.environ.setdefault("ENV", "test")

MODEL_PATH = Path("models/readmission_model.joblib")


@pytest.fixture(scope="session")
def role_keys() -> dict[str, str]:
    return dict(ROLE_KEYS)


@pytest.fixture(scope="session")
def audit_log_path() -> Path:
    return Path(os.environ["AUDIT_LOG_PATH"])


@pytest.fixture(scope="session")
def inference_log_path() -> Path:
    return Path(os.environ["INFERENCE_LOG_PATH"])


@pytest.fixture(scope="session")
def require_model():
    if not MODEL_PATH.exists():
        pytest.skip("models/readmission_model.joblib not found - run training first")


@pytest.fixture(scope="session")
def client(require_model):
    from fastapi.testclient import TestClient

    from src.security.rbac import reset_cache

    reset_cache()
    from api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def sample_payload() -> dict:
    import json

    return json.loads(Path("data/sample_input.json").read_text())


def auth(role: str) -> dict[str, str]:
    """Header dict for a role's test key."""
    return {"X-API-Key": ROLE_KEYS[role]}


@pytest.fixture()
def seeded_inference_log(inference_log_path) -> Path:
    """Write >= 30 realistic inference rows so /monitor/drift has data."""
    import pandas as pd

    from src.features.schema import FEATURE_COLUMNS

    src = Path("data/processed/test.csv")
    if not src.exists():
        pytest.skip("data/processed/test.csv not found")
    df = pd.read_csv(src).reindex(columns=FEATURE_COLUMNS).head(60).copy()
    df["readmission_probability"] = 0.2
    df["prediction"] = 0
    df["risk_level"] = "Low"
    df["model_version"] = "test"
    df["timestamp"] = "2026-01-01T00:00:00+00:00"
    inference_log_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(inference_log_path, index=False)
    return inference_log_path
