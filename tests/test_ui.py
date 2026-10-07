"""Tests for the browser UI (ui/) and the two endpoints it relies on.

* /ui/ is served from the same origin under a strict Content-Security-Policy
  (only our own scripts/styles, requests only to this API).
* /whoami authenticates (401 without a valid key) but needs no permission, and
  returns exactly the role's permissions from configs/access_control.yaml.
* /schema/features is public and matches src/features/schema.py, so the form
  limits are the same ones the server validates.
* /predict/explain returns real feature names (regression: they were f0, f1...).
"""

import re

import pytest

from src.features.schema import CATEGORY_VALUES, FEATURE_COLUMNS, NUMERIC_RANGES
from tests.conftest import auth


def test_ui_redirect_and_page(client):
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].endswith("/ui/")

    page = client.get("/ui/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "Readmission Risk Console" in page.text
    # No inline script/style, so the strict CSP can work.
    assert "<script>" not in page.text
    assert 'style="' not in page.text


def test_ui_csp_is_strict(client):
    csp = client.get("/ui/").headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.parametrize("asset", ["app.js", "styles.css"])
def test_ui_assets_served(client, asset):
    assert client.get(f"/ui/{asset}").status_code == 200


def test_whoami_requires_valid_key(client):
    assert client.get("/whoami").status_code == 401
    assert client.get("/whoami", headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize(
    "role, expected",
    [
        ("viewer", ["model:read"]),
        ("clinician", ["model:read", "predict:single", "predict:explain"]),
        ("admin", ["*"]),
    ],
)
def test_whoami_returns_role_permissions(client, role, expected):
    r = client.get("/whoami", headers=auth(role))
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == role
    assert body["permissions"] == expected
    assert auth(role)["X-API-Key"] not in r.text  # never echoes the key back


def test_feature_schema_matches_source_of_truth(client):
    r = client.get("/schema/features")
    assert r.status_code == 200
    s = r.json()
    assert s["feature_order"] == FEATURE_COLUMNS
    assert s["categorical"] == {k: list(v) for k, v in CATEGORY_VALUES.items()}
    assert {k: tuple(v) for k, v in s["numeric"].items()} == {
        k: tuple(v) for k, v in NUMERIC_RANGES.items()
    }


def test_explain_returns_real_feature_names(client, sample_payload):
    r = client.post("/predict/explain", json=sample_payload, headers=auth("admin"))
    assert r.status_code == 200
    names = [f["feature"] for f in r.json()["top_features"]]
    assert names
    assert not any(re.fullmatch(r"f\d+", n) for n in names), names
