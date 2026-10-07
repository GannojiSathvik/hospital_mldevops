# Risk Assessment

> Educational project on synthetic data. Not for clinical use.

Likelihood and impact are rated Low / Medium / High for a hypothetical real
deployment. "Residual" is the rating after the listed controls.

## 1. Clinical and model risks

| # | Risk | Likelihood | Impact | Controls in this repo | Residual |
|---|------|-----------|--------|-----------------------|----------|
| C1 | Model used for real clinical decisions | Medium | High | Disclaimer in README, API root response, OpenAPI description, image label, model/data cards | Medium — a disclaimer is not a technical control |
| C2 | Missed high-risk patients (false negatives: 47 of 208 on test) | High | High | Recall-oriented threshold (0.1327, test recall 0.774); recall >= 0.75 requirement | Medium |
| C3 | Alert fatigue from false positives (386 on test, precision 0.29) | High | Medium | Three-level risk bands so Medium vs High can be prioritised | Medium |
| C4 | Performance does not transfer to real data | High | High | Documented as synthetic; would require validation on governed real data | High (until validated) |
| C5 | Data / concept drift | Medium | High | Evidently drift (daily), labelled-performance check, retrain policy, champion/challenger, human PR review | Low |
| C6 | Unfair performance across groups | Unknown | High | None yet — no fairness audit; `insurance_type` could act as a proxy | High |
| C7 | Uncalibrated probabilities misread by clinicians | Medium | Medium | Isotonic calibration; calibration curve | Low |
| C8 | Explanations hard to interpret | Medium | Low | SHAP top-10 per request with named features; global SHAP plot | Medium |

## 2. Security and privacy risks

| # | Risk | Likelihood | Impact | Controls | Residual |
|---|------|-----------|--------|----------|----------|
| S1 | Leaked API key | Medium | High | Least-privilege roles, rate limit, audit log, `AuthFailureBurst` alert, keys only in env/secrets | Medium (static keys, no expiry) |
| S2 | Bulk data exfiltration | Low | High | Batch only for analyst/ml_engineer/admin, 5 MB / 10,000-row caps, audit | Low |
| S3 | Model poisoning / unauthorised retraining | Low | High | Pipeline RBAC (fail-closed), CI negative test, `trained_by` + data hash, PR review of retrained models | Low |
| S4 | Secret committed to git | Medium | High | `.gitignore`, Gitleaks (pre-commit + CI), fake demo values | Low |
| S5 | Vulnerable dependency or base image | Medium | High | Pinned deps, pip-audit, Trivy (CRITICAL gate), Dependabot, SBOM | Low (once CI runs) |
| S6 | Information leakage via errors | Low | Medium | Generic 500s, no value echo on 422, docs off in production | Low |
| S7 | Timing attack on keys | Low | Medium | SHA-256 + `hmac.compare_digest`, no early exit | Low |
| S8 | Container breakout / privilege escalation | Low | High | Non-root, read-only FS, dropped capabilities, seccomp, no-new-privileges | Low |
| S9 | Unapproved production deployment | Low | High | GitHub `production` environment with required reviewers | Low (once configured on GitHub) |
| S10 | Audit log tampering or loss | Medium | Medium | JSON lines with request IDs; **local file only** | Medium — ship to central append-only storage |

## 3. Operational risks

| # | Risk | Controls | Residual |
|---|------|----------|----------|
| O1 | API down / model not loaded | `/health`, `/ready` (503 until loaded), K8s probes, 2+ replicas, `ApiDown` alert | Low |
| O2 | Latency spike | `prediction_latency_seconds` histogram, `HighPredictionLatencyP95` alert, HPA 2-6 replicas | Low |
| O3 | Bad model promoted automatically | Promotion only if challenger PR-AUC >= champion; backup to `models/archive/`; PR review | Low |
| O4 | Pipelines/security tooling never actually run | **Open:** workflows not yet executed on GitHub; Docker, Trivy, Checkov, Gitleaks, Terraform not run locally | High until run |

## 4. Top actions before any real use

1. Validate on real, de-identified data under governance approval; perform a fairness audit.
2. Replace API keys with OIDC/JWT and a secrets manager with rotation.
3. Centralise audit and inference logs.
4. Run all workflows and scanners and fix findings.
5. Clinical review of threshold and risk bands with end users.
