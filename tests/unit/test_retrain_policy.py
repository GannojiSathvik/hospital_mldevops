"""Unit tests for src/monitoring/retrain_policy.py."""

from src.monitoring.retrain_policy import should_retrain

CONFIG = {
    "drift": {"dataset_drift_share_threshold": 0.30},
    "retraining": {"roc_auc_min": 0.70, "prediction_shift_max": 0.10},
}


def test_no_trigger_when_everything_healthy():
    retrain, reasons = should_retrain(
        0.10, current_roc_auc=0.80, prediction_shift=0.02, config=CONFIG
    )
    assert retrain is False
    assert reasons == []


def test_drift_share_trigger():
    retrain, reasons = should_retrain(0.40, config=CONFIG)
    assert retrain is True
    assert len(reasons) == 1 and "drift" in reasons[0]


def test_drift_share_exactly_at_threshold_does_not_trigger():
    assert should_retrain(0.30, config=CONFIG)[0] is False


def test_roc_auc_trigger():
    retrain, reasons = should_retrain(0.0, current_roc_auc=0.65, config=CONFIG)
    assert retrain is True
    assert "ROC-AUC" in reasons[0]


def test_prediction_shift_trigger():
    retrain, reasons = should_retrain(0.0, prediction_shift=0.15, config=CONFIG)
    assert retrain is True
    assert "shift" in reasons[0]


def test_missing_metrics_are_skipped():
    # No labels and no shift info -> only drift can fire.
    assert should_retrain(0.05, current_roc_auc=None, prediction_shift=None, config=CONFIG) == (
        False,
        [],
    )


def test_all_triggers_report_all_reasons():
    retrain, reasons = should_retrain(0.9, current_roc_auc=0.5, prediction_shift=0.5, config=CONFIG)
    assert retrain is True
    assert len(reasons) == 3


def test_reads_repo_config_by_default():
    # Uses configs/monitoring_config.yaml (tests run from the repo root).
    assert should_retrain(0.99)[0] is True
