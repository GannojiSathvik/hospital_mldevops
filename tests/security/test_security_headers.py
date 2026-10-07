"""Secure HTTP headers, request IDs and "no stack traces in responses".

We force an internal error by making the prediction service raise, then check
the client only receives a generic message plus the request ID.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth

EXPECTED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@pytest.mark.parametrize("path", ["/", "/health", "/model-info"])
def test_secure_headers_present(client, path):
    r = client.get(path, headers=auth("viewer"))
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers.get(name) == value, name
    assert "default-src" in r.headers["Content-Security-Policy"]
    assert "max-age" in r.headers["Strict-Transport-Security"]


def test_headers_on_error_responses(client):
    r = client.get("/model-info")  # 401
    assert r.status_code == 401
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers.get("X-Request-ID")


def test_request_id_generated(client):
    r = client.get("/health")
    assert len(r.headers["X-Request-ID"]) >= 8


def test_request_id_echoed(client):
    r = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_unsafe_request_id_replaced(client):
    r = client.get("/health", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert r.headers["X-Request-ID"] != "<script>alert(1)</script>"


def test_no_stack_trace_on_internal_error(client, sample_payload, monkeypatch):
    from api import prediction_service

    def boom(*args, **kwargs):
        raise RuntimeError("secret db password=hunter2 at /srv/app/config.py")

    monkeypatch.setattr(prediction_service.service, "predict_one", boom)
    r = client.post(
        "/predict", json=sample_payload, headers={**auth("admin"), "X-Request-ID": "err-req-1"}
    )
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error", "request_id": "err-req-1"}
    assert "Traceback" not in r.text and "hunter2" not in r.text
    assert r.headers["X-Content-Type-Options"] == "nosniff"
