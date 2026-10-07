# Model Card — 30-Day Readmission Risk

> **Educational/demo model trained on synthetic data. Not for clinical
> diagnosis or treatment decisions without professional validation.**

## Model details

| Item | Value |
|------|-------|
| Name | `CalibratedLogisticRegression` (registry name `healthcare-readmission`) |
| Version | 1.0.0 |
| Trained at | 2026-10-06T11:07:57Z (`models/model_metadata.json`) |
| Algorithm | Logistic Regression, `C=0.03`, `class_weight="balanced"`, wrapped in `CalibratedClassifierCV` (isotonic, cv=3) |
| Pipeline | `FeatureEngineer` -> ColumnTransformer (numeric: median impute -> 1%-99% clipping -> standard scaling; binary: most-frequent impute; categorical: most-frequent impute -> one-hot) -> calibrated classifier |
| Artifact | `models/readmission_model.joblib` (one sklearn `Pipeline`) |
| Decision threshold | 0.1327, strategy `max_precision_at_recall>=0.78` (`models/threshold.json`) |
| Training data | `data/processed/train.csv`, 4,800 rows, SHA-256 `d3678a4a…9dfd` |
| Trained by | `local-dev` (current artifacts were produced with pipeline RBAC disabled; `make train` records the ml_engineer principal) |
| MLflow | run `ca266d3d92c34043b8b839162191f692` in `mlflow.db` |

## Intended use

- **Intended:** teaching and demonstrating an ML lifecycle with DevSecOps and access control; ranking synthetic patients by readmission risk.
- **Not intended:** any real clinical decision, triage, insurance or resource denial. The model has never seen real patient data.

## Model selection

Three candidates were tuned with `RandomizedSearchCV` (10 iterations,
stratified 3-fold CV, scoring = average precision/PR-AUC) over the full
pipeline. Selection used **cross-validated PR-AUC** so the test set stayed
untouched until the final evaluation. Class imbalance: `class_weight="balanced"`
(LR, RF) and `scale_pos_weight` = negatives/positives (XGBoost).

| Model | CV PR-AUC | Test ROC-AUC | Test PR-AUC | Test recall @0.5 | Test precision @0.5 |
|-------|-----------|--------------|-------------|------------------|---------------------|
| **Logistic Regression** | **0.5079** | 0.7657 | 0.4551 | 0.6635 | 0.3433 |
| XGBoost | 0.5004 | 0.7689 | 0.4478 | 0.6635 | 0.3651 |
| Random Forest | 0.4803 | 0.7501 | 0.4118 | 0.5817 | 0.4047 |

(Comparison metrics are for the uncalibrated tuned models at a 0.5 threshold;
source `models/metrics.json`, `reports/evaluation/model_comparison.csv`.)
Logistic Regression won and is also the easiest to explain.

## Why calibration and a custom threshold

- **Calibration**: class weighting inflates probabilities. Isotonic calibration maps scores to observed frequencies, so a probability can be read as a risk estimate. See `reports/figures/calibration_curve.png`.
- **Threshold**: chosen on out-of-fold *training* predictions as the highest-precision point with recall >= 0.78 (requirement 0.75 + 0.03 margin, `params.yaml`), so test recall stays >= 0.75.

## Final test performance (1,200 patients, 208 readmitted)

| Metric | Value |
|--------|-------|
| ROC-AUC | 0.7648 |
| PR-AUC | 0.454 (baseline = prevalence ≈ 0.173) |
| Recall | 0.774 |
| Precision | 0.2943 |
| F1 | 0.4265 |
| Accuracy | 0.6392 |
| Confusion matrix | TN 606 · FP 386 · FN 47 · TP 161 |

Reading it: 161 of 208 readmissions are flagged; 47 are missed; about 3 in 10
flagged patients are actually readmitted. Figures: `reports/figures/`
(`roc_curve.png`, `pr_curve.png`, `confusion_matrix.png`,
`calibration_curve.png`, `feature_importance.png`, `shap_summary.png`).

## Risk levels

`src/models/risk.py`: Low if p < 0.1327 (prediction 0); Medium if 0.1327 <= p
< 0.60; High if p >= 0.60. Anchoring the Low band on the threshold guarantees a
patient is never labelled "High Risk of 30-Day Readmission" with risk level
"Low". (The original contract proposed fixed bands at 0.30/0.60, which could
contradict the prediction at this low threshold.)

## Explainability

`/predict/explain` returns the 10 largest SHAP (permutation) contributions in
probability units. Global importance: `reports/figures/feature_importance.png`
and `shap_summary.png`. Per-request features are named after the preprocessed
columns (e.g. `num__age`, `cat__insurance_type_Private`).

## Limitations and ethical considerations

- Synthetic data: relationships were designed by the generator (`src/data/generate.py`), so the model mostly re-learns those formulas; performance says nothing about real hospitals.
- Moderate discrimination (ROC-AUC ≈ 0.76) and low precision: many false alarms by design.
- No fairness evaluation across gender, age or insurance type has been performed; `insurance_type` is a model input and could act as a socioeconomic proxy in real data.
- Concept drift (e.g. new discharge programmes) will degrade performance; see monitoring.

## Monitoring and maintenance

Drift checks (Evidently, daily in CI), retraining policy (drift share > 0.30,
ROC-AUC < 0.70, positive-rate shift > 0.10), champion/challenger promotion
only if PR-AUC does not drop. Last demo: challenger (calibrated XGBoost)
PR-AUC 0.4288 < champion 0.454 -> champion kept. Details: `docs/monitoring.md`.
