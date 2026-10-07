# Lab Record — Internal Project 2: Healthcare 30-Day Readmission DevSecMLOps

> Educational project on synthetic data. Not for clinical use.

All commands are run from the repository root. `.venv/bin/python` is the
project's Python 3.11 interpreter. The Makefile loads `.env` (or
`.env.example`) so demo API keys are available to every `make` target.

---

# Experiment 5 — Build a Secure CI/CD Pipeline using GitHub Actions

## Aim

To build a CI/CD pipeline with GitHub Actions that automatically lints, type-checks
and tests a machine-learning API, scans it for security problems (code,
dependencies, secrets, infrastructure-as-code, container image), produces a
Software Bill of Materials, and deploys to staging automatically and to
production only after manual approval.

## Tools used

| Tool | Purpose |
|------|---------|
| GitHub Actions | CI/CD runner (8 workflow files in `.github/workflows/`) |
| ruff | Linter and formatter |
| mypy | Static type checker |
| pytest + pytest-cov | Tests and coverage |
| Bandit | Python static application security testing (SAST) |
| pip-audit | Known-vulnerability check of pinned Python packages |
| Gitleaks | Secret detection in files and git history |
| Checkov | Infrastructure-as-code misconfiguration scanner |
| CodeQL | GitHub's semantic code analysis |
| Trivy | CVE scanner for filesystem and container image |
| Syft (anchore/sbom-action) | SBOM generation (SPDX format) |
| cosign | Keyless container image signing |
| Docker | Container build (multi-stage, non-root) |
| Dependabot, pre-commit, CODEOWNERS | Dependency updates, local hooks, required reviewers |

## Theory

- **CI (Continuous Integration)** — every change is automatically built and tested before merge, so errors are caught early and cheaply.
- **CD (Continuous Delivery/Deployment)** — tested changes are automatically packaged and delivered to an environment.
- **DevSecOps / "shift left"** — security checks run inside the pipeline on every change instead of in a separate review at the end.
- **Security gate** — a pipeline step that fails the build when a scanner finds a problem above a threshold. Here: Bandit MEDIUM+ severity and confidence; any pip-audit vulnerability; any Gitleaks leak; any failed Checkov check; Trivy any **CRITICAL** fixable CVE.
- **Least-privilege token** — each workflow starts with `permissions: contents: read`; jobs request only the extra scopes they need (e.g. `packages: write` to push an image).
- **SBOM (Software Bill of Materials)** — list of every package inside the image, used to answer "are we affected by CVE X?".
- **Environment protection** — the GitHub `production` environment requires a reviewer to click "Approve and deploy" before the job runs and before its secrets are released.

Full explanation of every workflow and gate: `docs/ci_cd_pipeline.md`.

## Procedure

1. Install the environment:
   ```bash
   make install
   cp .env.example .env
   ```
2. Install the local pre-commit hooks (ruff, bandit, gitleaks, detect-private-key):
   ```bash
   make pre-commit
   ```
3. Generate data and a model (CI does the same before running API tests):
   ```bash
   make generate-data
   make train
   ```
4. Run the code-quality stage of `ci.yml` locally:
   ```bash
   make lint
   make type-check
   ```
5. Run the test stage:
   ```bash
   make test
   ```
6. Run the security stage of `security.yml` locally:
   ```bash
   make security-scan
   ```
7. Run everything CI runs, in one command:
   ```bash
   make ci
   ```
8. Build the container image (requires Docker running; models must exist):
   ```bash
   make docker-build
   ```
9. On GitHub (when the repository is pushed):
   1. Push to a branch and open a pull request to `main`; observe `CI`, `Security`, `Data Validation` and `Docker Publish` checks.
   2. Settings -> Branches: protect `main`, require these checks and a CODEOWNERS review.
   3. Settings -> Secrets and variables -> Actions: add `API_KEYS` (e.g. `ci-trainer:ml_engineer:<random>`) and `PIPELINE_API_KEY` (`<random>`), generated with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   4. Settings -> Environments: create `staging` (no reviewers) and `production` (required reviewers, prevent self-review).
   5. Merge the PR; observe `Docker Publish` pushing to GHCR, then `Deploy` running staging and pausing for approval on production.
   6. Open the Security -> Code scanning tab to see SARIF results.

## Output / Observations

Observed locally:

| Check | Observation |
|-------|-------------|
| `make test` | 135 tests collected and passing (unit, integration, security, UI endpoints) |
| Model produced by the pipeline | Calibrated Logistic Regression, threshold 0.1327; test ROC-AUC 0.7648, PR-AUC 0.454, recall 0.774, precision 0.2943, accuracy 0.6392 |
| Coverage report | `reports/coverage_html/index.html`, `reports/coverage.xml` |

Not yet observed (record honestly in the viva):

| Item | Status |
|------|--------|
| Gitleaks, Trivy, Checkov, actionlint, terraform | Not installed locally; `make security-scan` prints "not installed - skipped" for the first three |
| `docker build` | Not run: Docker daemon was not running |
| GitHub Actions runs, SARIF upload, GHCR push, approval gate | Workflows not yet executed on GitHub |

Screenshots to take for the record:

1. Terminal output of `make lint`, `make type-check`, `make test` (summary line) and `make security-scan`.
2. `reports/coverage_html/index.html` in a browser.
3. The workflow files list in `.github/workflows/` and the `permissions:` block of one workflow.
4. Once on GitHub: the PR checks list, the Actions run graph of `docker-publish.yml`, the "Review deployments" approval dialog for `production`, and the Security -> Code scanning page.

## Result

A secure CI/CD pipeline was implemented as eight GitHub Actions workflows that
lint, type-check, test, scan code/dependencies/secrets/IaC/images, generate an
SBOM, sign and publish the image, and deploy with manual approval for
production. The local equivalents (`make ci`) run successfully for lint, type
check, tests, Bandit and pip-audit; the remaining scanners and the GitHub
execution are pending as listed above.

## Viva questions

1. **What is the difference between CI and CD?** CI builds and tests every change; CD packages and ships tested changes to an environment.
2. **Why put security scanning in the pipeline?** Problems are found when they are cheapest to fix, and no change can skip the checks.
3. **What does each scanner find?** Bandit: insecure Python patterns. pip-audit: vulnerable dependencies. Gitleaks: committed secrets. Checkov: IaC misconfiguration. CodeQL: data-flow bugs like injection. Trivy: CVEs in files and images.
4. **Why fail only on CRITICAL in Trivy?** Balances safety and speed; lower severities are still reported and Dependabot proposes upgrades. `ignore-unfixed` avoids blocking on CVEs that have no fix yet.
5. **What is an SBOM and why sign the image?** SBOM lists every package for fast CVE impact checks; a cosign signature proves the image came from this workflow and was not altered.
6. **How is production protected?** The `production` environment requires a reviewer's approval; its secrets are released only after approval; it needs staging to succeed first.
7. **How are secrets handled?** Only through GitHub secrets / env vars, masked in logs, never in code or image; `.env` is git-ignored; Gitleaks checks history.
8. **Why `permissions: contents: read`?** If a dependency in a job is compromised, the token cannot push code or publish images.
9. **Why separate workflow files?** Different triggers, permissions and owners; a scheduled drift job must not block a pull request.
10. **What is `persist-credentials: false`?** The checkout token is not left in `.git/config` where later steps could steal it.

---

# Experiment 6 — ML Pipeline with Access Control

## Aim

To protect a machine-learning prediction API and its training/retraining
pipelines with role-based access control (RBAC), so that each user role can
perform only the actions its job requires, every access decision is audited,
and the controls are tested automatically and enforced in CI.

## Tools used

| Tool | Purpose |
|------|---------|
| FastAPI dependencies (`Depends`, `APIKeyHeader`) | Per-route authentication and authorization |
| PyYAML | RBAC policy file `configs/access_control.yaml` |
| Python `hashlib` (SHA-256), `hmac.compare_digest` | Key hashing and constant-time comparison |
| JSON-lines audit log | `logs/audit.log` |
| Prometheus counter `auth_failures_total{reason}` | Visibility of rejected requests |
| pytest | RBAC matrix and audit tests (`tests/security/test_rbac.py`) |
| GitHub Actions | `rbac-negative-test` job in `model-training.yml` |
| scikit-learn, MLflow, Prefect | The ML pipeline being protected |

## Theory

- **Authentication** — verifying who the caller is (here: an `X-API-Key` header). Failure -> **401 Unauthorized**.
- **Authorization** — deciding whether that caller may perform the action. Failure -> **403 Forbidden**.
- **RBAC** — permissions are attached to roles; each caller holds one role.
- **Least privilege** — each role gets only what its job needs, limiting damage from a leaked key.
- **Deny by default** — anything not explicitly allowed is refused.
- **Fail-closed** — if configuration is missing or wrong, access is denied rather than granted. Pipeline RBAC is disabled only by the exact value `ENFORCE_PIPELINE_RBAC=false`.
- **Timing attack** — learning a secret from how long a comparison takes; prevented with `hmac.compare_digest`.
- **Audit log** — tamper-evident record of who did what and when.

Roles and permissions (`configs/access_control.yaml`):

| Role | Permissions |
|------|-------------|
| viewer | model:read |
| clinician | model:read, predict:single, predict:explain |
| analyst | model:read, predict:single, predict:batch, monitor:drift |
| ml_engineer | analyst's + pipeline:train, pipeline:validate, pipeline:retrain, model:register |
| admin | * |

Full explanation: `docs/access_control.md`.

## Procedure

1. Prepare keys (the demo file contains one key per role):
   ```bash
   cp .env.example .env
   ```
2. Run the access-controlled data pipeline as ml_engineer (Makefile exports `PIPELINE_API_KEY=demo-ml-engineer-key-change-me`, `ENFORCE_PIPELINE_RBAC=true`):
   ```bash
   make generate-data
   make validate-data      # needs pipeline:validate
   make train              # needs pipeline:train
   ```
3. Show that a non-engineer cannot train (denied before any data is read):
   ```bash
   set -a; source .env; set +a
   PIPELINE_API_KEY=demo-viewer-key-change-me .venv/bin/python -m pipelines.training_pipeline
   echo "exit code: $?"
   ```
4. Start the API:
   ```bash
   make run-api
   ```
5. In a second terminal, run the access-control demo:
   ```bash
   BASE=http://127.0.0.1:8000
   curl -s -w "\n%{http_code}\n" -X POST $BASE/predict -H "Content-Type: application/json" -d @data/sample_input.json
   curl -s -w "\n%{http_code}\n" -X POST $BASE/predict -H "X-API-Key: not-a-real-key" -H "Content-Type: application/json" -d @data/sample_input.json
   curl -s -w "\n%{http_code}\n" -X POST $BASE/predict -H "X-API-Key: demo-viewer-key-change-me" -H "Content-Type: application/json" -d @data/sample_input.json
   curl -s -w "\n%{http_code}\n" -X POST $BASE/predict -H "X-API-Key: demo-clinician-key-change-me" -H "Content-Type: application/json" -d @data/sample_input.json
   curl -s -w "\n%{http_code}\n" -X POST $BASE/predict/batch -H "X-API-Key: demo-clinician-key-change-me" -F "file=@data/sample_batch_input.csv;type=text/csv"
   curl -s -X POST $BASE/predict/batch -H "X-API-Key: demo-analyst-key-change-me" -F "file=@data/sample_batch_input.csv;type=text/csv" -o predictions.csv
   ```
6. Inspect the audit trail and the auth-failure metric:
   ```bash
   tail -n 8 logs/audit.log
   curl -s $BASE/metrics | grep auth_failures_total
   ```
7. Run the automated access-control tests:
   ```bash
   make test-security
   ```
8. Drift-triggered retraining under RBAC (`pipeline:retrain`):
   ```bash
   .venv/bin/python -m scripts.seed_monitoring_data --mode drifted --with-labels
   make monitor
   make retrain
   .venv/bin/python -m scripts.seed_monitoring_data --mode normal   # reset afterwards
   ```
9. (On GitHub) Run `Model Training` manually from the Actions tab; observe `rbac-negative-test` pass (viewer denied) before `train` starts.

## Output / Observations

Step 3 (pipeline denial) — from `pipelines/training_pipeline.py` and `src/security/rbac.py`:

```
ACCESS DENIED: Role 'viewer' is not permitted to perform 'pipeline:train'
exit code: 2
```

Step 5 (API) — observed responses:

| Request | Status | Body |
|---------|--------|------|
| No key | 401 | `{"detail":"Missing API key"}` |
| Wrong key | 401 | `{"detail":"Invalid API key"}` |
| viewer -> /predict | 403 | `{"detail":"Role 'viewer' lacks permission 'predict:single'"}` |
| clinician -> /predict | 200 | `{"prediction":1,"prediction_label":"High Risk of 30-Day Readmission","readmission_probability":0.7793,"risk_level":"High","model_version":"1.0.0","timestamp":"..."}` |
| clinician -> /predict/batch | 403 | `{"detail":"Role 'clinician' lacks permission 'predict:batch'"}` |
| analyst -> /predict/batch | 200 | CSV with 20 rows: `patient_id,prediction,prediction_label,readmission_probability,risk_level,model_version,prediction_timestamp` |

Step 6 — example audit lines (real):

```json
{"timestamp": "2026-10-06T11:06:45.988113+00:00", "principal": "demo-clinician", "role": "clinician", "action": "predict:batch", "resource": "POST /predict/batch", "allowed": false, "reason": "forbidden", "request_id": "1234dd0c-379e-4c80-8e1c-84cc54319397", "client_ip": "127.0.0.1"}
{"timestamp": "2026-10-06T11:08:10.011484+00:00", "principal": "view", "role": "viewer", "action": "pipeline:train", "resource": "pipeline", "allowed": false}
```

Step 7 — `tests/security/test_rbac.py` asserts the full 5 x 5 role/endpoint
matrix, 401 for missing and wrong keys (with `WWW-Authenticate: ApiKey`), 200
for public endpoints, audit lines for allow and deny, no raw keys in the audit
log, only hashes in memory, unknown roles rejected, and the pipeline guard. All
135 project tests pass.

Step 8 — drift and retraining (`reports/drift/retrain_decision.json`,
`reports/retraining/decision.json`):

| Scenario | Drifted columns | Other signals | Decision |
|----------|----------------|---------------|----------|
| Normal traffic (500 rows) | 0 / 25 (share 0.00) | positive-rate shift 0.0237 | no retraining |
| Drifted traffic | 13 / 25 (share 0.52) | ROC-AUC 0.542 < 0.70; shift 0.516 > 0.10 | retrain |
| Champion/challenger | — | challenger (calibrated XGBoost) PR-AUC 0.4288 vs champion 0.454 | champion kept |

Note: the recorded retraining run was made with `ENFORCE_PIPELINE_RBAC=false`
(`"triggered_by": "local-dev (RBAC disabled)"`). With `make retrain` the
`triggered_by` field records the ml_engineer principal instead.

Screenshots to take:

1. The curl outputs showing 401, 401, 403, 200, 403, 200.
2. `tail logs/audit.log` with allowed and denied lines.
3. The terminal showing `ACCESS DENIED ... exit code: 2`.
4. `make test-security` summary.
5. `configs/access_control.yaml`.
6. `/metrics` lines for `auth_failures_total`.
7. Once on GitHub: the `Model Training` run graph with `rbac-negative-test` -> `train`.

## Result

Role-based access control was implemented for both the prediction API and the
ML pipelines. Unauthenticated requests receive 401, unauthorized roles receive
403, permitted roles receive results, and every decision is recorded in
`logs/audit.log` without exposing keys. Training, validation, retraining and
model registration are restricted to ml_engineer and admin, enforced fail-closed,
and verified by automated tests and a CI negative test.

## Viva questions

1. **Difference between authentication and authorization?** Who you are (401 on failure) vs what you may do (403 on failure).
2. **Why can a clinician not use batch prediction?** One stolen clinician key could score and export a whole population (up to 10,000 rows per upload) — bulk data exfiltration. Clinicians only need one patient at a time.
3. **Why can only ml_engineer and admin train?** To prevent model poisoning and to make every model traceable via `trained_by`.
4. **How are keys protected?** Supplied via env var, hashed with SHA-256 immediately, compared with `hmac.compare_digest`, never logged or echoed.
5. **What is a timing attack?** Inferring a secret from response time differences; constant-time comparison prevents it.
6. **What does fail-closed mean here?** If `ENFORCE_PIPELINE_RBAC` is unset or misspelled, RBAC stays on; only `false` turns it off.
7. **What is in an audit line?** Timestamp, principal name, role, action, resource, allowed, reason, request ID, client IP.
8. **How is RBAC tested?** Parametrized pytest matrix over 5 roles x 5 endpoints, plus 401/audit/pipeline tests, plus the CI `rbac-negative-test` job.
9. **Why are `/health` and `/metrics` public?** Probes and Prometheus need them without secrets; they return no patient data.
10. **What would production use instead of API keys?** OAuth2/OIDC with short-lived JWTs, a secrets manager with key rotation, and centralised audit storage.
