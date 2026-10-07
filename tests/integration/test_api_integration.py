"""Integration tests: batch CSV round-trip and safe-upload rules.

* A real CSV goes in, a CSV with exactly the contract columns comes out, one
  row per patient, patient IDs preserved.
* Oversized upload -> 413, wrong file type -> 400, missing columns -> 400.
"""

from __future__ import annotations

import io

import pandas as pd

from tests.conftest import auth

BATCH_COLUMNS = [
    "patient_id",
    "prediction",
    "prediction_label",
    "readmission_probability",
    "risk_level",
    "model_version",
    "prediction_timestamp",
]


def _upload(client, content: bytes, name="batch.csv", ctype="text/csv", role="analyst"):
    return client.post("/predict/batch", headers=auth(role), files={"file": (name, content, ctype)})


def test_batch_round_trip(client):
    src = pd.read_csv("data/sample_batch_input.csv", dtype={"patient_id": str})
    r = _upload(client, open("data/sample_batch_input.csv", "rb").read())
    assert r.status_code == 200, r.text
    out = pd.read_csv(io.StringIO(r.text), dtype={"patient_id": str})
    assert list(out.columns) == BATCH_COLUMNS
    assert len(out) == len(src)
    assert out["patient_id"].tolist() == src["patient_id"].tolist()
    assert out["readmission_probability"].between(0, 1).all()
    assert set(out["risk_level"]) <= {"Low", "Medium", "High"}


def test_batch_too_large(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")  # ~1 KB
    content = open("data/sample_batch_input.csv", "rb").read()
    assert len(content) > 1100
    r = _upload(client, content)
    assert r.status_code == 413


def test_batch_too_many_rows(client, monkeypatch):
    monkeypatch.setenv("MAX_BATCH_ROWS", "5")
    r = _upload(client, open("data/sample_batch_input.csv", "rb").read())
    assert r.status_code == 413


def test_batch_wrong_extension(client):
    r = _upload(client, b"not,a,csv", name="evil.exe", ctype="application/octet-stream")
    assert r.status_code == 400


def test_batch_wrong_content_type(client):
    r = _upload(client, b"a,b\n1,2\n", name="data.csv", ctype="application/x-msdownload")
    assert r.status_code == 400


def test_batch_missing_columns(client):
    r = _upload(client, b"patient_id,age\nP1,50\n")
    assert r.status_code == 400
    assert "Missing required columns" in r.text


def test_batch_invalid_category(client):
    df = pd.read_csv("data/sample_batch_input.csv")
    df.loc[0, "gender"] = "Robot"
    r = _upload(client, df.to_csv(index=False).encode())
    assert r.status_code == 400


def test_model_info_integration(client):
    r = client.get("/model-info", headers=auth("viewer"))
    assert r.status_code == 200
    body = r.json()
    assert body["model_name"]
    assert body["model_version"] == "1.0.0"
