"""Automated retraining policy: decide *whether* the model should be retrained.

The rule is deliberately simple and auditable. Retrain if ANY of these is true
(thresholds come from configs/monitoring_config.yaml):

1. Data drift:        dataset drift share  > drift.dataset_drift_share_threshold (0.30)
2. Concept drift:     ROC-AUC on labelled production data < retraining.roc_auc_min (0.70)
3. Prediction shift:  |change in high-risk prediction rate| > retraining.prediction_shift_max (0.10)

Each fired rule adds a human-readable reason, which is written to
reports/drift/retrain_decision.json so reviewers can see *why* it retrained.
"""

from __future__ import annotations

from src.monitoring.drift import load_monitoring_config


def should_retrain(
    drift_share: float,
    current_roc_auc: float | None = None,
    prediction_shift: float | None = None,
    config: dict | None = None,
) -> tuple[bool, list[str]]:
    """Return (retrain?, reasons). Metrics passed as None are simply skipped
    (e.g. no labelled data yet -> ROC-AUC rule cannot fire)."""
    if config is None:
        config = load_monitoring_config()
    drift_max = float(config.get("drift", {}).get("dataset_drift_share_threshold", 0.30))
    retrain_cfg = config.get("retraining", {})
    auc_min = float(retrain_cfg.get("roc_auc_min", 0.70))
    shift_max = float(retrain_cfg.get("prediction_shift_max", 0.10))

    reasons: list[str] = []
    if drift_share is not None and drift_share > drift_max:
        reasons.append(f"dataset drift share {drift_share:.2f} > threshold {drift_max:.2f}")
    if current_roc_auc is not None and current_roc_auc < auc_min:
        reasons.append(f"ROC-AUC {current_roc_auc:.3f} < minimum {auc_min:.2f}")
    if prediction_shift is not None and prediction_shift > shift_max:
        reasons.append(
            f"prediction positive-rate shift {prediction_shift:.3f} > maximum {shift_max:.2f}"
        )
    return (len(reasons) > 0, reasons)
