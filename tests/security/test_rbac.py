"""Lab 6 - Access-control tests.

The heart of this file is the role x endpoint matrix: for every role and every
protected endpoint we assert the exact outcome (200 allowed vs 403 forbidden).
We also check authentication failures (401), the audit trail, that raw keys
never appear in the audit log, and the pipeline guard used by CLI pipelines.
"""

from __future__ import annotations

import json

import pytest

from src.security import rbac
from tests.conftest import ROLE_KEYS, auth

ROLES = ["viewer", "clinician", "analyst", "ml_engineer", "admin"]

# Expected access: endpoint -> set of roles that should get 200.
MATRIX = {
    ("GET", "/model-info"): {"viewer", "clinician", "analyst", "ml_engineer", "admin"},
    ("POST", "/predict"): {"clinician", "analyst", "ml_engineer", "admin"},
    ("POST", "/predict/explain"): {"clinician", "admin"},
    ("POST", "/predict/batch"): {"analyst", "ml_engineer", "admin"},
    ("POST", "/monitor/drift"): {"analyst", "ml_engineer", "admin"},
}


def _call(client, method, path, role, payload):
    headers = auth(role) if role else {}
    if path in ("/predict", "/predict/explain"):
        return client.post(path, json=payload, headers=headers)
    if path == "/predict/batch":
        with open("data/sample_batch_input.csv", "rb") as fh:
            return client.post(path, headers=headers, files={"file": ("b.csv", fh, "text/csv")})
    return client.request(method, path, headers=headers)


# ------------------------------------------------------------- policy unit tests
def test_policy_roles_match_contract():
    rbac.reset_cache()
    policy = rbac.load_policy()
    assert set(policy["roles"]) == set(ROLES)
    assert rbac.has_permission("admin", "anything:at_all")  # wildcard
    assert rbac.has_permission("ml_engineer", "pipeline:train")
    assert not rbac.has_permission("clinician", "predict:batch")
    assert not rbac.has_permission("viewer", "predict:single")
    assert not rbac.has_permission("nonexistent", "model:read")  # deny by default


def test_unknown_role_rejected_at_load(tmp_path):
    bad = tmp_path / "policy.yaml"
    bad.write_text(
        "allowed_roles: [viewer]\nroles:\n  viewer: {permissions: ['model:read']}\n"
        "  superuser: {permissions: ['*']}\n"
    )
    with pytest.raises(ValueError):
        rbac.load_policy(str(bad))


def test_unknown_role_in_api_keys_rejected(monkeypatch):
    monkeypatch.setenv("API_KEYS", "eve:superuser:abc")
    rbac.reset_cache()
    with pytest.raises(ValueError):
        rbac.resolve_api_key("abc")
    rbac.reset_cache()


def test_resolve_api_key():
    rbac.reset_cache()
    p = rbac.resolve_api_key(ROLE_KEYS["clinician"])
    assert p == rbac.Principal(name="clinician-user", role="clinician")
    assert rbac.resolve_api_key("wrong") is None
    assert rbac.resolve_api_key("") is None
    assert rbac.resolve_api_key(None) is None


def test_only_hashes_kept_in_memory():
    rbac.reset_cache()
    rbac.resolve_api_key("x")
    dump = repr(rbac._key_table_cache)
    for key in ROLE_KEYS.values():
        assert key not in dump


# ------------------------------------------------------------ pipeline guard
def test_pipeline_permission_allowed_for_ml_engineer(monkeypatch):
    monkeypatch.setenv("PIPELINE_API_KEY", ROLE_KEYS["ml_engineer"])
    principal = rbac.require_pipeline_permission("pipeline:train")
    assert principal.role == "ml_engineer"


def test_pipeline_permission_denied_for_clinician(monkeypatch):
    monkeypatch.setenv("PIPELINE_API_KEY", ROLE_KEYS["clinician"])
    with pytest.raises(PermissionError):
        rbac.require_pipeline_permission("pipeline:train")


def test_pipeline_permission_missing_key(monkeypatch):
    monkeypatch.delenv("PIPELINE_API_KEY", raising=False)
    with pytest.raises(PermissionError):
        rbac.require_pipeline_permission("pipeline:retrain")


# ---------------------------------------------------------------- API matrix
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("endpoint", list(MATRIX), ids=lambda e: f"{e[0]} {e[1]}")
def test_rbac_matrix(client, sample_payload, seeded_inference_log, role, endpoint):
    if endpoint[1] == "/monitor/drift":
        pytest.importorskip("src.monitoring.drift")
    method, path = endpoint
    r = _call(client, method, path, role, sample_payload)
    expected = 200 if role in MATRIX[endpoint] else 403
    assert r.status_code == expected, f"{role} {method} {path}: {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("endpoint", list(MATRIX), ids=lambda e: f"{e[0]} {e[1]}")
def test_missing_key_is_401(client, sample_payload, endpoint):
    r = _call(client, endpoint[0], endpoint[1], None, sample_payload)
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "ApiKey"


def test_wrong_key_is_401(client, sample_payload):
    r = client.post("/predict", json=sample_payload, headers={"X-API-Key": "not-a-real-key"})
    assert r.status_code == 401
    assert "not-a-real-key" not in r.text


@pytest.mark.parametrize("path", ["/", "/health", "/ready", "/metrics"])
def test_public_endpoints_need_no_key(client, path):
    assert client.get(path).status_code == 200


# --------------------------------------------------------------- audit trail
def _audit_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_audit_log_written_for_allow_and_deny(client, sample_payload, audit_log_path):
    client.post("/predict", json=sample_payload, headers=auth("clinician"))
    with open("data/sample_batch_input.csv", "rb") as fh:
        client.post(
            "/predict/batch", headers=auth("clinician"), files={"file": ("b.csv", fh, "text/csv")}
        )
    lines = _audit_lines(audit_log_path)
    allowed = [
        x
        for x in lines
        if x["principal"] == "clinician-user" and x["action"] == "predict:single" and x["allowed"]
    ]
    denied = [
        x
        for x in lines
        if x["principal"] == "clinician-user"
        and x["action"] == "predict:batch"
        and not x["allowed"]
    ]
    assert allowed and denied
    assert {"timestamp", "role", "resource"} <= set(denied[-1])
    assert denied[-1]["reason"] == "forbidden"


def test_raw_keys_never_in_audit_log(client, sample_payload, audit_log_path):
    client.post("/predict", json=sample_payload, headers={"X-API-Key": "leaky-wrong-key"})
    client.get("/model-info", headers=auth("admin"))
    text = audit_log_path.read_text()
    assert "leaky-wrong-key" not in text
    for key in ROLE_KEYS.values():
        assert key not in text


def test_auth_failures_metric(client):
    client.get("/model-info")
    assert 'auth_failures_total{reason="missing_key"}' in client.get("/metrics").text
