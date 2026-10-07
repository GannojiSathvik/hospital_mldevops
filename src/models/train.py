"""Model training: compare 3 models, tune, calibrate, pick a threshold, save artifacts.

Step by step (this is what to explain in the viva):
1. Load the stratified train/test CSVs produced by the preprocess step.
2. For each candidate (Logistic Regression, Random Forest, XGBoost) run a small
   RandomizedSearchCV over the FULL pipeline (feature engineering +
   preprocessing + classifier) with StratifiedKFold(3), scoring = PR-AUC.
   Tuning the whole pipeline means imputation/scaling are re-learned inside
   each CV fold -> no leakage from validation folds.
   Class imbalance: class_weight="balanced" (LR/RF), scale_pos_weight (XGB).
3. Select the best model by mean cross-validated PR-AUC (NOT by test score;
   the test set is only used for the final, honest report).
4. Wrap the winner in CalibratedClassifierCV (isotonic, cv=3): class weighting
   distorts probabilities, calibration fixes them so "0.40" really means
   ~40% observed readmission — important because clinicians read the number.
5. Choose the decision threshold on out-of-fold TRAINING predictions:
   best precision subject to recall >= 0.75 (see src/models/evaluate.py).
6. Evaluate on the test set, write figures/reports, save every artifact in
   models/, and log params/metrics/artifacts of every run to MLflow.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import (
    ParameterGrid,
    RandomizedSearchCV,
    StratifiedKFold,
    cross_val_predict,
)
from sklearn.pipeline import Pipeline

from src.data.preprocess import read_patient_csv
from src.features.schema import FEATURE_COLUMNS, TARGET
from src.models import evaluate as ev
from src.models.pipeline_factory import MODEL_NAMES, build_classifier, build_pipeline

MODEL_VERSION = "1.0.0"
DISPLAY_NAMES = {
    "logistic_regression": "LogisticRegression",
    "random_forest": "RandomForestClassifier",
    "xgboost": "XGBClassifier",
}


def load_yaml(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def file_sha256(path: str | Path) -> str:
    """Fingerprint of the training data file -> lets us prove which data a model was trained on."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _mlflow():
    """Configure and return the mlflow module (tracking URI from env, sqlite by default)."""
    import mlflow

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
    mlflow.set_experiment("healthcare-readmission")
    return mlflow


def tune_model(name, X, y, train_params, model_cfg, scale_pos_weight) -> RandomizedSearchCV:
    """RandomizedSearchCV for one candidate model over the full pipeline."""
    clip = model_cfg.get("outlier_clipping", {})
    pipe = build_pipeline(
        build_classifier(name, train_params["random_state"], scale_pos_weight),
        clip.get("lower_quantile", 0.01),
        clip.get("upper_quantile", 0.99),
    )
    space = {f"classifier__{k}": v for k, v in model_cfg["models"][name]["params"].items()}
    n_iter = min(train_params["n_iter"], len(ParameterGrid(space)))
    search = RandomizedSearchCV(
        pipe,
        space,
        n_iter=n_iter,
        scoring=train_params["scoring"],
        cv=StratifiedKFold(
            train_params["cv_folds"], shuffle=True, random_state=train_params["random_state"]
        ),
        random_state=train_params["random_state"],
        n_jobs=-1,
        refit=True,
    )
    search.fit(X, y)
    return search


def build_calibrated_pipeline(best_pipeline: Pipeline, method: str, cv: int) -> Pipeline:
    """Same steps as the tuned pipeline, but the classifier is wrapped in CalibratedClassifierCV."""
    tuned_clf = clone(best_pipeline.named_steps["classifier"])
    return Pipeline(
        [
            ("features", clone(best_pipeline.named_steps["features"])),
            ("preprocessor", clone(best_pipeline.named_steps["preprocessor"])),
            ("classifier", CalibratedClassifierCV(tuned_clf, method=method, cv=cv)),
        ]
    )


def run_training(
    train_path: str = "data/processed/train.csv",
    test_path: str = "data/processed/test.csv",
    models_dir: str | Path = "models",
    figures_dir: str | Path = "reports/figures",
    evaluation_dir: str | Path = "reports/evaluation",
    params: dict | None = None,
    model_cfg: dict | None = None,
    model_names: list[str] | None = None,
    trained_by: str = "local-dev",
    log_to_mlflow: bool = True,
) -> dict:
    """Train, select, calibrate, evaluate and save. Returns a summary dict."""
    t0 = time.time()
    params = params or load_yaml("params.yaml")
    model_cfg = model_cfg or load_yaml("configs/model_config.yaml")
    tp = params["train"]
    model_names = model_names or MODEL_NAMES
    models_path, figures_path, evaluation_path = (
        Path(models_dir),
        Path(figures_dir),
        Path(evaluation_dir),
    )
    for d in (models_path, figures_path, evaluation_path):
        d.mkdir(parents=True, exist_ok=True)

    train_df, test_df = read_patient_csv(train_path), read_patient_csv(test_path)
    X_train, y_train = train_df[FEATURE_COLUMNS], train_df[TARGET].astype(int)
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df[TARGET].astype(int)
    scale_pos_weight = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))
    data_hash = file_sha256(train_path)

    mlflow = _mlflow() if log_to_mlflow else None
    parent = (
        mlflow.start_run(run_name=f"training-{datetime.now():%Y%m%d-%H%M%S}") if mlflow else None
    )

    # ---- 1. tune + compare candidates ------------------------------------------------
    searches, comparison = {}, []
    for name in model_names:
        start = time.time()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            search = tune_model(name, X_train, y_train, tp, model_cfg, scale_pos_weight)
        searches[name] = search
        test_proba = search.best_estimator_.predict_proba(X_test)[:, 1]
        test_m = ev.compute_metrics(y_test, test_proba, 0.5)
        row = {
            "model": name,
            "cv_pr_auc": round(float(search.best_score_), 4),
            **{
                f"test_{k}": v
                for k, v in test_m.items()
                if k not in ("confusion_matrix", "threshold")
            },
            "train_seconds": round(time.time() - start, 1),
            "best_params": json.dumps(
                {k.replace("classifier__", ""): v for k, v in search.best_params_.items()}
            ),
        }
        comparison.append(row)
        print(
            f"[{name}] cv PR-AUC={row['cv_pr_auc']:.4f} test ROC-AUC={test_m['roc_auc']:.4f} "
            f"test PR-AUC={test_m['pr_auc']:.4f} ({row['train_seconds']}s)"
        )
        if mlflow:
            with mlflow.start_run(run_name=name, nested=True):
                mlflow.log_params(
                    {
                        "model": name,
                        **{
                            k.replace("classifier__", ""): v for k, v in search.best_params_.items()
                        },
                    }
                )
                mlflow.log_metric("cv_pr_auc", search.best_score_)
                mlflow.log_metrics(
                    {f"test_{k}_at_0.5": v for k, v in test_m.items() if isinstance(v, float)}
                )

    comparison_df = pd.DataFrame(comparison).sort_values("cv_pr_auc", ascending=False)
    comparison_df.to_csv(evaluation_path / "model_comparison.csv", index=False)
    best_name = comparison_df.iloc[0]["model"]
    best_search = searches[best_name]
    print(f"Selected best model by CV PR-AUC: {best_name}")

    # ---- 2. calibrate the winner ------------------------------------------------------
    final_model = build_calibrated_pipeline(
        best_search.best_estimator_, tp["calibration_method"], tp["calibration_cv"]
    )
    cv = StratifiedKFold(tp["cv_folds"], shuffle=True, random_state=tp["random_state"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # Out-of-fold probabilities on TRAIN -> used only to choose the threshold.
        oof_proba = cross_val_predict(
            clone(final_model), X_train, y_train, cv=cv, method="predict_proba"
        )[:, 1]
        final_model.fit(X_train, y_train)

    # ---- 3. threshold + test evaluation ---------------------------------------------
    # A small safety margin on the recall target so recall stays >= min_recall on unseen data.
    target_recall = min(tp["min_recall"] + tp.get("recall_margin", 0.0), 0.99)
    threshold, strategy = ev.choose_threshold(y_train, oof_proba, target_recall)
    test_proba = final_model.predict_proba(X_test)[:, 1]
    uncal_proba = best_search.best_estimator_.predict_proba(X_test)[:, 1]
    best_metrics = ev.compute_metrics(y_test, test_proba, threshold)
    print(f"Threshold={threshold:.4f} ({strategy}) -> test metrics: {best_metrics}")

    # ---- 4. reports + figures --------------------------------------------------------
    (evaluation_path / "classification_report.txt").write_text(
        f"Model: {best_name} (calibrated, {tp['calibration_method']})\n"
        f"Threshold: {threshold:.4f} ({strategy})\n\n"
        + ev.text_report(y_test, test_proba, threshold)
    )
    ev.plot_confusion_matrix(y_test, test_proba, threshold, figures_path / "confusion_matrix.png")
    ev.plot_roc(y_test, test_proba, figures_path / "roc_curve.png")
    ev.plot_pr(y_test, test_proba, threshold, figures_path / "pr_curve.png")
    ev.plot_calibration(
        y_test,
        {f"{best_name} (uncalibrated)": uncal_proba, f"{best_name} (calibrated)": test_proba},
        figures_path / "calibration_curve.png",
    )
    _feature_importance_plot(
        best_search.best_estimator_, best_name, figures_path / "feature_importance.png"
    )
    if tp.get("shap_sample_size", 0) > 0:
        _shap_summary_plot(
            best_search.best_estimator_, X_train, tp, figures_path / "shap_summary.png"
        )

    # ---- 5. save artifacts (names fixed by CONTRACT.md) -------------------------------
    per_model = {r["model"]: {k: v for k, v in r.items() if k != "model"} for r in comparison}
    metrics_doc = {
        "best_model": best_name,
        "selection_metric": "cv_pr_auc",
        "threshold": threshold,
        "threshold_strategy": strategy,
        "best_model_test_metrics": best_metrics,
        "models": per_model,
    }
    joblib.dump(final_model, models_path / "readmission_model.joblib")
    joblib.dump(
        final_model[:-1], models_path / "preprocessor.joblib"
    )  # features + preprocessor, fitted
    (models_path / "threshold.json").write_text(
        json.dumps({"threshold": threshold, "strategy": strategy}, indent=2)
    )
    (models_path / "feature_list.json").write_text(json.dumps(FEATURE_COLUMNS, indent=2))
    (models_path / "metrics.json").write_text(json.dumps(metrics_doc, indent=2))
    metadata = {
        "model_name": f"Calibrated{DISPLAY_NAMES.get(best_name, best_name)}",
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": FEATURE_COLUMNS,
        "threshold": threshold,
        "metrics": best_metrics,
        "training_data_hash": data_hash,
        "trained_by": trained_by,
        "best_params": json.loads(comparison_df.iloc[0]["best_params"]),
        "calibration": tp["calibration_method"],
        "n_train_rows": int(len(train_df)),
    }

    # ---- 6. MLflow: log final model + artifacts -----------------------------------------
    if mlflow:
        mlflow.log_params(
            {
                "best_model": best_name,
                "threshold": round(threshold, 4),
                "threshold_strategy": strategy,
                "calibration": tp["calibration_method"],
                "training_data_hash": data_hash[:16],
                "trained_by": trained_by,
            }
        )
        mlflow.log_metrics({k: v for k, v in best_metrics.items() if isinstance(v, float)})
        mlflow.log_artifacts(str(figures_path), artifact_path="figures")
        mlflow.log_artifact(
            str(evaluation_path / "model_comparison.csv"), artifact_path="evaluation"
        )
        mlflow.log_artifact(
            str(evaluation_path / "classification_report.txt"), artifact_path="evaluation"
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # MLflow 3 serialises sklearn models with skops (safer than pickle) and refuses
            # types it does not know. We explicitly trust only the types in OUR pipeline.
            import skops.io

            trusted = skops.io.get_untrusted_types(data=skops.io.dumps(final_model))
            info = mlflow.sklearn.log_model(
                final_model,
                name="model",
                input_example=X_train.head(3),
                skops_trusted_types=trusted,
            )
        # `parent` is always set when mlflow is enabled; the check narrows the type for mypy.
        if parent is not None:
            metadata["mlflow_run_id"] = parent.info.run_id
        metadata["mlflow_model_uri"] = info.model_uri
        mlflow.end_run()

    (models_path / "model_metadata.json").write_text(json.dumps(metadata, indent=2))
    if mlflow:
        with mlflow.start_run(run_id=metadata["mlflow_run_id"]):
            for f in ("metrics.json", "threshold.json", "model_metadata.json", "feature_list.json"):
                mlflow.log_artifact(str(models_path / f), artifact_path="model_files")
    print(f"Training finished in {time.time() - t0:.1f}s; artifacts in {models_path}/")
    return {
        "best_model": best_name,
        "threshold": threshold,
        "metrics": best_metrics,
        "comparison": comparison_df,
        "metadata": metadata,
    }


def _feature_importance_plot(pipeline: Pipeline, name: str, path: Path) -> None:
    """Built-in importances (trees) or |coefficients| (logistic regression) of the tuned model."""
    clf = pipeline.named_steps["classifier"]
    names = pipeline.named_steps["preprocessor"].get_feature_names_out()
    if hasattr(clf, "feature_importances_"):
        imp, label = clf.feature_importances_, "importance"
    else:
        imp, label = np.abs(clf.coef_[0]), "|coefficient| (standardised features)"
    ev.plot_feature_importance(names, imp, path, f"Top features - {name} ({label})")


def _shap_summary_plot(pipeline: Pipeline, X: pd.DataFrame, tp: dict, path: Path) -> None:
    """SHAP summary on a sample of rows. Explains the tuned (uncalibrated) model: calibration
    is a monotonic rescaling, so the feature ranking/direction is the same."""
    import matplotlib.pyplot as plt
    import shap

    sample = X.sample(min(tp["shap_sample_size"], len(X)), random_state=tp["random_state"])
    Xt = pipeline[:-1].transform(sample)
    names = pipeline.named_steps["preprocessor"].get_feature_names_out()
    clf = pipeline.named_steps["classifier"]
    if hasattr(clf, "feature_importances_"):
        explainer = shap.TreeExplainer(clf)
    else:
        explainer = shap.LinearExplainer(clf, Xt)
    values = np.asarray(explainer.shap_values(Xt))
    if values.ndim == 3:  # (rows, features, classes) -> keep the positive class
        values = values[:, :, 1]
    plt.figure()
    shap.summary_plot(values, Xt, feature_names=list(names), show=False, max_display=20)
    plt.tight_layout()
    plt.savefig(path, dpi=120, bbox_inches="tight")
    plt.close("all")
