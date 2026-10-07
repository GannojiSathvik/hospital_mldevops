"""Shared helpers that turn a probability into user-facing risk outputs.

Kept in one tiny module so the API, batch scoring and reports all use the
SAME cut-offs.

The "Low" band ends at the tuned decision threshold (models/threshold.json),
so risk_level and prediction can never contradict each other:
    p <  threshold        -> "Low"     (prediction 0)
    threshold <= p < 0.60 -> "Medium"  (prediction 1: flagged, follow up)
    p >= 0.60             -> "High"    (prediction 1: strongest signal)
Why: the threshold is deliberately low (~0.13) to catch >= 75% of
readmissions. With fixed 0.30 bands a patient could be predicted
"High Risk of 30-Day Readmission" yet shown risk_level "Low".
"""

LOW_CUTOFF = 0.30
HIGH_CUTOFF = 0.60

POSITIVE_LABEL = "High Risk of 30-Day Readmission"
NEGATIVE_LABEL = "Low Risk of 30-Day Readmission"


def risk_level(p: float, threshold: float = LOW_CUTOFF) -> str:
    """Map a readmission probability to "Low" / "Medium" / "High".

    Pass the model's decision threshold so "Low" == "not flagged".
    """
    if p < min(threshold, HIGH_CUTOFF):
        return "Low"
    if p < HIGH_CUTOFF:
        return "Medium"
    return "High"


def prediction_from_probability(p: float, threshold: float) -> int:
    """1 if the probability reaches the decision threshold, else 0."""
    return int(p >= threshold)


def prediction_label(prediction: int) -> str:
    return POSITIVE_LABEL if prediction == 1 else NEGATIVE_LABEL
