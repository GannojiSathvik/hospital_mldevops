# Architecture

> Educational project on synthetic data. Not for clinical use.

## 1. End-to-end lifecycle

```mermaid
flowchart TD
    A[Dataset<br/>scripts/generate_dataset.py -> data/raw/healthcare_readmission.csv] --> B[Data Validation<br/>pipelines/validation_pipeline.py, configs/validation_config.yaml]
    B --> C[Preprocessing and Feature Engineering<br/>scripts/preprocess.py, src/features/engineering.py]
    C --> D[Model Training<br/>pipelines/training_pipeline.py -> src/models/train.py]
    D --> E[MLflow Experiment Tracking<br/>sqlite:///mlflow.db, experiment healthcare-readmission]
    E --> F[Model Registry<br/>scripts/register_model.py, name healthcare-readmission]
    F --> G[Security and Quality Gate<br/>ci.yml, security.yml]
    G --> H[Docker Image<br/>docker-publish.yml: Trivy, SBOM, cosign]
    H --> I[Staging Deployment<br/>deploy.yml staging]
    I --> J[Production API<br/>deploy.yml production, manual approval]
    J --> K[Prometheus and Grafana Monitoring<br/>/metrics, alert_rules.yml]
    K --> L[Evidently Drift Detection<br/>pipelines/monitoring_pipeline.py]
    L --> M[Automated Retraining<br/>pipelines/retraining_pipeline.py]
    M -. champion/challenger, PR to main .-> F
```

Lab 6 access control sits across this flow: validation, training, retraining
and registration call `require_pipeline_permission(...)`, and the production
API checks every protected request with `require_permission(...)`.

## 2. CI/CD flow

```mermaid
flowchart LR
    dev[Commit] --> hooks[pre-commit<br/>ruff, bandit, gitleaks]
    hooks --> pr[Pull request]
    pr --> ci[ci.yml<br/>lint, mypy, tests]
    pr --> sec[security.yml<br/>Bandit, pip-audit, Gitleaks,<br/>Checkov, CodeQL, Trivy fs]
    pr --> dv[data-validation.yml]
    pr --> img[docker-publish.yml<br/>build + Trivy image + SBOM]
    ci & sec & dv & img --> gate{checks green +<br/>CODEOWNERS review}
    gate --> main[merge to main]
    main --> train[model-training.yml<br/>rbac-negative-test -> train]
    main --> pub[docker-publish.yml<br/>push GHCR + cosign]
    pub --> stg[deploy.yml staging]
    stg --> appr{{reviewer approval}}
    appr --> prod[deploy.yml production]
    cron1([daily]) --> drift[drift-monitoring.yml]
    cron2([weekly]) --> rt[retraining.yml<br/>RBAC pipeline:retrain -> PR]
```

Details: `docs/ci_cd_pipeline.md`.

## 3. Runtime components

```mermaid
flowchart LR
    client[Client<br/>X-API-Key] --> mw[RequestContextMiddleware<br/>request ID, secure headers,<br/>metrics, safe 500]
    mw --> rl[slowapi rate limit<br/>60/minute on /predict*]
    rl --> authz[require_permission<br/>401 / 403 + audit log]
    authz --> routes[api/main.py routes]
    routes --> svc[PredictionService<br/>models/readmission_model.joblib<br/>threshold.json, metadata]
    svc --> inflog[(data/inference/<br/>inference_log.csv)]
    authz --> audit[(logs/audit.log)]
    routes --> drift[src/monitoring/drift.py<br/>Evidently]
    drift --> rep[(reports/drift/)]
    prom[Prometheus :9090] -->|scrape /metrics| mw
    graf[Grafana :3000] --> prom
```

## 4. Component responsibilities

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Schema | `src/features/schema.py` | Single source of truth for column names, allowed categories and numeric ranges, shared by validation, training and the API. |
| Data | `src/data/generate.py`, `preprocess.py`, `validation.py` | Synthetic data, stratified split, rule-based validation (columns, types, categories, missing %, ranges, duplicate IDs, schema change, target balance, leakage). |
| Features | `src/features/engineering.py` | `FeatureEngineer` (pulse pressure, utilization score, BP/glucose/polypharmacy flags) and `OutlierClipper` (1st-99th percentile). |
| Model | `src/models/pipeline_factory.py`, `train.py`, `evaluate.py`, `risk.py` | Builds the single sklearn `Pipeline`, tunes 3 models, calibrates, picks the threshold, writes artifacts and figures, maps probability to risk level. |
| Monitoring | `src/monitoring/drift.py`, `performance.py`, `retrain_policy.py` | Evidently drift report, labelled-performance check, retrain decision. |
| Security | `src/security/rbac.py`, `api/security.py` | Lab 6 RBAC and audit. |
| API | `api/*` | HTTP layer, validation, logging, metrics. |
| Orchestration | `pipelines/*`, `dvc.yaml` | Prefect flows (set `USE_PREFECT=false` to run as plain Python) and DVC stages for reproducibility. |

## 5. Key design decisions

- **One Pipeline object for training and serving.** Imputation, clipping, scaling and encoding are fitted once and saved with the classifier, so the API cannot apply different preprocessing from training (no "training/serving skew").
- **Selection by cross-validated PR-AUC, not test score.** The test set is used once for the final report, so reported numbers are honest.
- **Calibration.** Class weighting distorts probabilities; isotonic calibration makes a reported 0.40 mean roughly 40% observed risk.
- **Recall-oriented threshold** chosen on out-of-fold training predictions (0.1327).
- **Fail-safe API start-up.** If artifacts are missing the API still starts; `/ready` returns 503 so an orchestrator does not route traffic to it.
- **Security as configuration.** Roles in YAML, keys in env vars, thresholds in `configs/monitoring_config.yaml` — reviewers can see policy without reading code.
