"""Tests for the shared risk-level helper (bands anchored on the decision threshold)."""

import pytest

from src.models.risk import prediction_from_probability, prediction_label, risk_level


@pytest.mark.parametrize(
    "p, expected",
    [
        (0.0, "Low"),
        (0.29, "Low"),
        (0.30, "Medium"),
        (0.59, "Medium"),
        (0.60, "High"),
        (1.0, "High"),
    ],
)
def test_risk_level_default_boundaries(p, expected):
    assert risk_level(p) == expected


@pytest.mark.parametrize("p", [0.0, 0.05, 0.13, 0.2, 0.5, 0.6, 0.99])
def test_risk_level_never_contradicts_prediction(p):
    threshold = 0.13
    flagged = prediction_from_probability(p, threshold) == 1
    assert (risk_level(p, threshold) != "Low") == flagged


def test_prediction_and_label():
    assert prediction_from_probability(0.5, 0.4) == 1
    assert prediction_from_probability(0.3, 0.4) == 0
    assert prediction_label(1) == "High Risk of 30-Day Readmission"
    assert prediction_label(0) == "Low Risk of 30-Day Readmission"
