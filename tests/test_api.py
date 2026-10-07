"""Happy-path tests for every API endpoint, using an admin key.

Checks status codes and the exact response shapes promised by the spec
(single prediction fields, model-info, explanation top-10, metrics names).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.conftest import auth

PREDICTION_KEYS = {
    "prediction",
    "prediction_label",
    "readmission_probability",
    "risk_level",
    "model_version",
    "timestamp",
}


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "disclaimer" in r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["model_loaded"] is True


def test_model_info(client):
    r = client.get("/model-info", headers=auth("admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["model_version"]
    assert 0 < body["threshold"] < 1
    assert len(body["feature_columns"]) == 25


def test_predict_shape(client, sample_payload):
    r = client.post("/predict", json=sample_payload, headers=auth("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == PREDICTION_KEYS
    assert body["prediction"] in (0, 1)
    assert body["prediction_label"] in (
        "High Risk of 30-Day Readmission",
        "Low Risk of 30-Day Readmission",
    )
    assert 0.0 <= body["readmission_probability"] <= 1.0
    assert body["readmission_probability"] == round(body["readmission_probability"], 4)
    assert body["risk_level"] in ("Low", "Medium", "High")
    assert datetime.fromisoformat(body["timestamp"]).tzinfo is not None


def test_predict_writes_inference_log(client, sample_payload, inference_log_path):
    client.post("/predict", json=sample_payload, headers=auth("admin"))
    assert inference_log_path.exists()
    assert "readmission_probability" in inference_log_path.read_text().splitlines()[0]


def test_predict_validation_error(client, sample_payload):
    bad = {**sample_payload, "age": 500, "gender": "Robot"}
    r = client.post("/predict", json=bad, headers=auth("admin"))
    assert r.status_code == 422
    fields = {e["loc"][-1] for e in r.json()["detail"]}
    assert {"age", "gender"} <= fields
    assert "Robot" not in r.text  # submitted values are not echoed back


def test_predict_rejects_extra_field(client, sample_payload):
    r = client.post("/predict", json={**sample_payload, "ssn": "123"}, headers=auth("admin"))
    assert r.status_code == 422


def test_predict_explain(client, sample_payload):
    r = client.post("/predict/explain", json=sample_payload, headers=auth("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= PREDICTION_KEYS
    assert len(body["top_features"]) == 10
    contribs = [abs(f["contribution"]) for f in body["top_features"]]
    assert contribs == sorted(contribs, reverse=True)


def test_predict_batch(client):
    with open("data/sample_batch_input.csv", "rb") as fh:
        r = client.post(
            "/predict/batch", headers=auth("admin"), files={"file": ("batch.csv", fh, "text/csv")}
        )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")


def test_monitor_drift(client, seeded_inference_log):
    pytest.importorskip("src.monitoring.drift")
    r = client.post("/monitor/drift", headers=auth("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert 0.0 <= body["dataset_drift_share"] <= 1.0
    assert isinstance(body["drift_detected"], bool)


def test_monitor_drift_not_enough_rows(client, inference_log_path):
    inference_log_path.write_text("age\n1\n")
    r = client.post("/monitor/drift", headers=auth("admin"))
    assert r.status_code == 400


def test_metrics(client, sample_payload):
    client.post("/predict", json=sample_payload, headers=auth("admin"))
    r = client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    for name in (
        "predictions_total",
        "prediction_latency_seconds",
        "http_requests_total",
        "api_errors_total",
        "high_risk_predictions_total",
        "auth_failures_total",
    ):
        assert name in text
