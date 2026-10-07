# Monitoring, Drift Detection and Automated Retraining

This document explains how the project notices that the readmission model may
no longer be trustworthy, and what it does about it. Every technical term is
defined the first time it appears.

## 1. Why monitor a model at all?

A **machine-learning model** learns patterns from historical data (the
**training data**). It only stays accurate while new patients resemble those
historical patients. Hospitals change: a new clinic opens, the population
ages, a new discharge programme starts. **Monitoring** means regularly checking
production data and model behaviour so we notice such changes before they hurt
patients.

Two kinds of change matter:

| Term | Plain meaning | Example | Needs true outcomes? |
|---|---|---|---|
| **Data drift** (also *covariate drift*) | The *inputs* change: the distribution of one or more features in production differs from training. | Patients are on average 15 years older. | No |
| **Concept drift** | The *relationship* between inputs and outcome changes: the same patient profile now has a different readmission risk. | A new follow-up phone-call programme lowers readmissions for diabetics. Inputs look the same, but the model's predictions become wrong. | Yes |

Data drift can be detected immediately from the inputs. Concept drift can only
be detected once the **ground truth** (the real outcome — was the patient
readmitted within 30 days?) is known, i.e. about 30 days later.

## 2. Where the data comes from

* **Reference data** — the data the model was trained on:
  `data/processed/train.csv`.
* **Current data** — what the model has seen in production. The API appends
  every prediction (the 25 input features + `readmission_probability` +
  `prediction` + `timestamp`) to `data/inference/inference_log.csv`.
* **Labelled feedback** (optional) — production patients whose real outcome is
  now known: `data/inference/labeled_feedback.csv`.

In the lab there is no real traffic, so `scripts/seed_monitoring_data.py`
simulates it (see section 7).

## 3. How data-drift detection works

We use **Evidently** (an open-source monitoring library, version 0.7).

### 3.1 Per-column test

For each of the 25 feature columns Evidently compares the reference values with
the current values using a **statistical test** — a calculation that answers
"how likely is it that these two samples come from the same distribution?".
The **distribution** of a column is simply how its values are spread out (e.g.
what fraction of patients are 20–30, 30–40 years old, ...).

Evidently picks the test automatically (`src/monitoring/drift.py` leaves the
choice to it unless `configs/monitoring_config.yaml` overrides it):

| Reference size | Column type | Default test | Column "drifted" when |
|---|---|---|---|
| ≤ 1000 rows | numeric, > 5 distinct values | **Kolmogorov–Smirnov (K-S) test** — compares the cumulative distributions and returns a *p-value* | p-value < 0.05 |
| ≤ 1000 rows | categorical with > 2 values | **Chi-squared test** — compares category counts | p-value < 0.05 |
| ≤ 1000 rows | binary (2 values) | **Z-test for proportions** — compares the share of 1s | p-value < 0.05 |
| > 1000 rows | numeric, > 5 distinct values | **Wasserstein distance** (normed) — "how much earth must be moved" to turn one histogram into the other, in units of standard deviations | distance ≥ 0.1 |
| > 1000 rows | categorical / binary | **Jensen–Shannon distance** — a 0-to-1 measure of how different two probability distributions are | distance ≥ 0.1 |

A **p-value** is the probability of seeing a difference at least this large if
the two samples really came from the same distribution. A tiny p-value (< 0.05)
means "very unlikely to be chance", so we call it drift.

Why distances for large data? With thousands of rows, p-value tests become so
sensitive that even meaningless tiny differences become "significant".
Distance metrics measure *how big* the change is instead, which is more useful
in practice. Our training split has ~4,000+ rows, so in this project numeric
columns use **Wasserstein** and categorical/binary columns use
**Jensen–Shannon**.

In our code, binary 0/1 flags (diabetes, follow_up_scheduled, ...) are
declared as *categorical*, because "share of patients with diabetes" is the
meaningful comparison, not the "average of 0s and 1s".

### 3.2 Dataset drift share

**Dataset drift share** = (number of drifted columns) ÷ (number of columns
checked). With 25 features, 8 drifted columns gives 8/25 = 0.32.

The dataset as a whole is considered **drifted** when this share is
**greater than 0.30** (`drift.dataset_drift_share_threshold`).

**Why 30%?** It is a deliberate balance:

* One or two drifting columns happen often by chance or for harmless reasons
  (seasonality, a single ward). Retraining for that would be noisy and costly.
* If roughly a third of all inputs have shifted, the patient population has
  genuinely changed, and the model is very likely being used outside the
  conditions it was validated for.
* Evidently's own default is 50%; for a clinical use case we prefer to react
  earlier. The value is a configuration choice and can be tuned.

### 3.3 Outputs

Each run of `run_drift_report()` writes to `reports/drift/`:

* `drift_report_<timestamp>.html` — interactive Evidently report (open in a browser)
* `drift_summary_<timestamp>.json` — our summary plus per-column test, score, threshold, and missing-value shares
* `latest_summary.json` — copy of the newest summary (used by the API's `/monitor/drift`)

and returns:

```json
{"dataset_drift_share": 0.32, "drifted_columns": ["age", "..."], "n_drifted": 8,
 "n_columns": 25, "drift_detected": true, "report_path": "reports/drift/drift_report_....html"}
```

## 4. Other monitored signals

* **Prediction distribution shift** — the **positive rate** (share of patients
  predicted "High Risk") in production vs. the positive rate the *same model*
  produces on the training data. We compare against the model's own training
  predictions (not the raw label rate) so both sides use the same decision
  threshold. Shift = |current rate − reference rate|.
* **Missing values** — share of empty cells per column in production data
  (included in the JSON summary).
* **Model performance (concept drift)** — when labelled feedback exists,
  `src/monitoring/performance.py` re-scores the model:
  * **ROC-AUC** — probability the model ranks a random readmitted patient
    above a random non-readmitted one (0.5 = coin flip, 1.0 = perfect).
  * **PR-AUC** (average precision) — area under the precision-recall curve;
    more informative than ROC-AUC when positives are rare (~18% here).
  * **Recall** — share of truly readmitted patients the model flagged.
* API latency, error rate, prediction volume and high-risk percentage are
  monitored separately through Prometheus/Grafana (see the API metrics).

## 5. Retraining triggers

`src/monitoring/retrain_policy.py::should_retrain()` returns
`(retrain: bool, reasons: list[str])`. Retraining is triggered if **any** of:

| Trigger | Config key | Default |
|---|---|---|
| Dataset drift share above threshold | `drift.dataset_drift_share_threshold` | > 0.30 |
| ROC-AUC on labelled data below minimum | `retraining.roc_auc_min` | < 0.70 |
| Positive-rate shift above maximum | `retraining.prediction_shift_max` | > 0.10 |

Safety rule: if fewer than `retraining.min_inference_rows` (30) predictions
have been logged, no decision is taken — too little evidence.

The decision and its reasons are saved to `reports/drift/retrain_decision.json`
so a reviewer can always see *why* the system retrained.

## 6. Retraining with champion/challenger

`pipelines/retraining_pipeline.py`:

1. **RBAC check** — the caller's `PIPELINE_API_KEY` must have the
   `pipeline:retrain` permission (ml_engineer or admin). Set
   `ENFORCE_PIPELINE_RBAC=false` only for local experiments.
2. Reads `reports/drift/retrain_decision.json`; stops if retraining is not
   needed (unless `--force`).
3. Merges labelled feedback (if any) into the training data.
4. Trains a new model.
5. **Champion/challenger comparison.** The *champion* is the model currently
   in production; the *challenger* is the newly trained one. Both are scored on
   the **same held-out test set**, and the challenger is **promoted only if its
   PR-AUC is not worse** than the champion's. Otherwise the champion is kept.
   This prevents an automated job from silently deploying a worse model.
6. Before any promotion, the old model artifacts are backed up to
   `models/archive/<timestamp>/` so a rollback is one copy away.
7. The outcome is written to `reports/retraining/decision.json`.

## 7. Running the demo

```bash
# 0. (once) generate data and train the model
#    see README / Makefile

# A. Healthy production traffic -> expect little or no drift, retrain=False
.venv/bin/python -m scripts.seed_monitoring_data --mode normal
.venv/bin/python -m pipelines.monitoring_pipeline

# B. Shifted population -> expect drift_detected=True, retrain=True
.venv/bin/python -m scripts.seed_monitoring_data --mode drifted --with-labels
.venv/bin/python -m pipelines.monitoring_pipeline

# C. Retrain (champion/challenger). Needs an ml_engineer/admin key, or:
ENFORCE_PIPELINE_RBAC=false .venv/bin/python -m pipelines.retraining_pipeline

# D. Reset to normal traffic afterwards
.venv/bin/python -m scripts.seed_monitoring_data --mode normal
```

Then open the newest `reports/drift/drift_report_*.html` in a browser.

The pipelines are **Prefect** flows. *Prefect* is a workflow orchestrator: it
runs Python functions as tracked *tasks* inside a *flow*, with logging, retries
and scheduling. No Prefect server is required — Prefect 3 starts a temporary
local one automatically. To bypass Prefect completely (plain Python), set
`USE_PREFECT=false`. `pipelines/orchestration.py` shows how to schedule
monitoring daily and retraining weekly; in this repo the same schedule is also
provided by GitHub Actions cron workflows.

## 8. Viva cheat-sheet

* *Data drift vs concept drift?* Inputs change vs input→outcome relationship changes; the latter needs labels.
* *Which test does Evidently use?* Auto: K-S / chi-squared / Z-test for ≤1000 reference rows; Wasserstein (numeric) and Jensen–Shannon (categorical) above that.
* *What is dataset drift share?* Fraction of feature columns flagged as drifted.
* *Why 30%?* Ignores isolated noisy columns but reacts once a third of the population profile has changed; stricter than Evidently's 50% default because of clinical risk.
* *Why champion/challenger?* Automated retraining must never deploy a worse model; we compare on the same test set and keep a backup.
