"""Model evaluation: metrics, clinical threshold selection and report figures.

Why not just accuracy? Only ~17% of patients are readmitted, so a model that
predicts "nobody is readmitted" is 83% accurate and clinically useless. We
therefore focus on:
- Recall (sensitivity): share of truly readmitted patients we flag. Missing a
  high-risk patient (false negative) means no follow-up call, no medication
  review -> a preventable readmission. That is the costly error here.
- Precision: share of flagged patients who really get readmitted (cost of
  false alarms = extra nurse calls, which is cheap by comparison).
- PR-AUC (average precision) and ROC-AUC: threshold-free ranking quality;
  PR-AUC is more informative under class imbalance.

Threshold strategy: among all thresholds with recall >= 0.75, pick the one
with the best precision (= the fewest false alarms while still catching 3 out
of 4 readmissions). The threshold is chosen on out-of-fold TRAINING
predictions, never on the test set, so the test metrics stay honest.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend: works in CI / Docker without a display
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.calibration import calibration_curve  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    accuracy_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)


def compute_metrics(y_true, proba, threshold: float = 0.5) -> dict:
    """All headline metrics for one set of predicted probabilities."""
    y_true = np.asarray(y_true).astype(int)
    pred = (np.asarray(proba) >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "accuracy": round(float(accuracy_score(y_true, pred)), 4),
        "precision": round(float(precision_score(y_true, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, proba)), 4),
        "pr_auc": round(float(average_precision_score(y_true, proba)), 4),
        "threshold": round(float(threshold), 4),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def choose_threshold(y_true, proba, min_recall: float = 0.75) -> tuple[float, str]:
    """Highest-precision threshold that still achieves recall >= min_recall.

    Returns (threshold, strategy_description). Falls back to the threshold
    with maximum F1 if no threshold reaches the recall target.
    """
    precision, recall, thresholds = precision_recall_curve(y_true, proba)
    # precision/recall have one more entry than thresholds; drop the final (recall=0) point.
    precision, recall = precision[:-1], recall[:-1]
    ok = recall >= min_recall
    if ok.any():
        idx = np.where(ok)[0]
        best = idx[np.argmax(precision[idx])]
        return float(thresholds[best]), f"max_precision_at_recall>={min_recall}"
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-12, None)
    return float(thresholds[np.argmax(f1)]), "max_f1_fallback"


def text_report(y_true, proba, threshold: float) -> str:
    pred = (np.asarray(proba) >= threshold).astype(int)
    return classification_report(
        y_true,
        pred,
        target_names=["Not readmitted (0)", "Readmitted (1)"],
        digits=4,
        zero_division=0,
    )


# ----------------------------------------------------------------------------- figures
def _save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_confusion_matrix(y_true, proba, threshold: float, path: Path) -> None:
    cm = confusion_matrix(y_true, (np.asarray(proba) >= threshold).astype(int), labels=[0, 1])
    fig, ax = plt.subplots(figsize=(4.5, 4))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(
            j, i, str(v), ha="center", va="center", color="white" if v > cm.max() / 2 else "black"
        )
    ax.set_xticks([0, 1], ["Pred 0", "Pred 1"])
    ax.set_yticks([0, 1], ["True 0", "True 1"])
    ax.set_title(f"Confusion matrix (threshold={threshold:.3f})")
    _save(fig, path)


def plot_roc(y_true, proba, path: Path) -> None:
    fpr, tpr, _ = roc_curve(y_true, proba)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(fpr, tpr, label=f"ROC-AUC = {roc_auc_score(y_true, proba):.3f}")
    ax.plot([0, 1], [0, 1], "--", color="grey", label="Random")
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate (recall)")
    ax.set_title("ROC curve (test set)")
    ax.legend()
    _save(fig, path)


def plot_pr(y_true, proba, threshold: float, path: Path) -> None:
    precision, recall, thresholds = precision_recall_curve(y_true, proba)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(recall, precision, label=f"PR-AUC = {average_precision_score(y_true, proba):.3f}")
    ax.axhline(np.mean(y_true), ls="--", color="grey", label="Baseline (prevalence)")
    pred = np.asarray(proba) >= threshold
    ax.scatter(
        [recall_score(y_true, pred)],
        [precision_score(y_true, pred, zero_division=0)],
        color="red",
        zorder=3,
        label=f"Chosen threshold {threshold:.3f}",
    )
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Precision-Recall curve (test set)")
    ax.legend()
    _save(fig, path)


def plot_calibration(y_true, curves: dict, path: Path) -> None:
    """curves: label -> probabilities. Shows how well predicted risk matches observed frequency."""
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot([0, 1], [0, 1], "--", color="grey", label="Perfectly calibrated")
    for label, proba in curves.items():
        frac_pos, mean_pred = calibration_curve(y_true, proba, n_bins=10, strategy="quantile")
        ax.plot(mean_pred, frac_pos, marker="o", label=label)
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed readmission rate")
    ax.set_title("Calibration curve (test set)")
    ax.legend()
    _save(fig, path)


def plot_feature_importance(names, importances, path: Path, title: str, top_n: int = 20) -> None:
    order = np.argsort(importances)[::-1][:top_n][::-1]
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.barh(np.asarray(names)[order], np.asarray(importances)[order])
    ax.set_title(title)
    _save(fig, path)


# ----------------------------------------------------------------------------- saved-model evaluation
def evaluate_saved_model(
    model_path: str = "models/readmission_model.joblib",
    threshold_path: str = "models/threshold.json",
    test_path: str = "data/processed/test.csv",
    figures_dir: str = "reports/figures",
    evaluation_dir: str = "reports/evaluation",
) -> dict:
    """Re-load the saved model and evaluate it on the held-out test set.

    Writes classification_report.txt + metrics.json to evaluation_dir and the
    confusion/ROC/PR/calibration plots to figures_dir. Returns the metrics dict.
    """
    import joblib

    from src.data.preprocess import read_patient_csv
    from src.features.schema import FEATURE_COLUMNS, TARGET

    model = joblib.load(model_path)
    threshold = json.loads(Path(threshold_path).read_text())["threshold"]
    test = read_patient_csv(test_path)
    y, proba = test[TARGET].to_numpy(), model.predict_proba(test[FEATURE_COLUMNS])[:, 1]

    metrics = compute_metrics(y, proba, threshold)
    fig_dir, ev_dir = Path(figures_dir), Path(evaluation_dir)
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "classification_report.txt").write_text(
        f"Test set classification report (threshold={threshold:.4f})\n\n"
        + text_report(y, proba, threshold)
    )
    (ev_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    plot_confusion_matrix(y, proba, threshold, fig_dir / "confusion_matrix.png")
    plot_roc(y, proba, fig_dir / "roc_curve.png")
    plot_pr(y, proba, threshold, fig_dir / "pr_curve.png")
    # For comparison, also show the un-calibrated base estimator (fold 1 of the calibration CV).
    curves = {"Calibrated model": proba}
    clf = model.named_steps.get("classifier") if hasattr(model, "named_steps") else None
    if clf is not None and hasattr(clf, "calibrated_classifiers_"):
        base = clf.calibrated_classifiers_[0].estimator
        curves = {
            "Uncalibrated base estimator": base.predict_proba(
                model[:-1].transform(test[FEATURE_COLUMNS])
            )[:, 1],
            **curves,
        }
    plot_calibration(y, curves, fig_dir / "calibration_curve.png")
    return metrics
