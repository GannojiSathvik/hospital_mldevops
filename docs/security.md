# Security

> Educational project on synthetic data. Not for clinical use.

This page summarises security controls by lifecycle stage and links to the
detailed documents. Vulnerability reporting policy: `SECURITY.md`.
CI security gates: `docs/ci_cd_pipeline.md` (Lab 5). Access control:
`docs/access_control.md` (Lab 6).

## 1. Threat model (short)

| Asset | Threat | Main controls |
|-------|--------|---------------|
| Prediction API | Unauthorised use, key guessing | API-key auth, RBAC (401/403), hashed keys with constant-time compare, rate limit, `auth_failures_total` metric + `AuthFailureBurst` alert |
| Patient inputs/outputs | Bulk exfiltration | Batch scoring only for analyst/ml_engineer/admin, 10,000-row and 5 MB caps, `Cache-Control: no-store`, audit log |
| Model | Poisoning, unreviewed replacement | Training/retraining/registration only for ml_engineer/admin, `trained_by` + training-data SHA-256 in metadata, champion/challenger, retraining opens a PR for human review |
| Secrets | Leak via code, logs, image | Env vars only, `.env` git-ignored, Gitleaks (pre-commit + CI), keys never logged, no secrets in Dockerfile |
| Dependencies / image | Known CVEs, tampering | Pinned versions, pip-audit, Trivy fs + image (fail on CRITICAL), Dependabot, SBOM, cosign signature |
| Infrastructure | Misconfiguration | Checkov on Terraform, Kubernetes, Dockerfile and workflows; hardened manifests |
| CI/CD | Token abuse, script injection | `permissions: contents: read` default, `persist-credentials: false`, no `pull_request_target`, inputs passed via `env:`, manual approval for production |

## 2. Secure coding (application)

- **Input validation** — Pydantic models (`api/schemas.py`) enforce ranges, binary 0/1, allowed categories and reject unknown fields (`extra="forbid"`). Batch CSVs are checked for columns, ranges and categories.
- **Safe file upload** — `.csv` extension and CSV content type required; at most `MAX_UPLOAD_MB` + 1 bytes are read (default 5 MB); `MAX_BATCH_ROWS` (default 10,000); parse errors return 400 without details.
- **No information leakage** — unexpected errors become `{"detail":"Internal server error","request_id":...}`; the stack trace goes only to the server log. Validation errors do not echo submitted values. `/docs`, `/redoc`, `/openapi.json` are disabled when `ENV=production`.
- **Secure headers** (`api/middleware.py`) — `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a strict `Content-Security-Policy` (relaxed only for the Swagger UI), `Strict-Transport-Security`, `Cache-Control: no-store`. Tested in `tests/security/test_security_headers.py`.
- **Request IDs** — every response has `X-Request-ID`; client-supplied IDs are accepted only if they match `^[A-Za-z0-9._-]{1,64}$` (prevents log injection).
- **Rate limiting** — slowapi, `RATE_LIMIT` (default `60/minute`) per hashed API key or IP -> 429.
- **Model loading** — joblib/pickle can execute code when loaded, so artifacts are only loaded from the project's own `models/` directory, never from uploads.
- **YAML** — always `yaml.safe_load`.

## 3. Secrets management

- Local: `.env` (git-ignored), created from `.env.example` whose values are obviously fake (`demo-*-key-change-me`) and allow-listed by exact value in `.gitleaks.toml`.
- CI: GitHub repository secrets `API_KEYS`, `PIPELINE_API_KEY`; environment secrets `KUBE_CONFIG_STAGING`, `KUBE_CONFIG_PRODUCTION`.
- Kubernetes: `readmission-api-secrets` Secret created out of band (example file excluded from Kustomize).
- Recommendation for real deployments: AWS Secrets Manager or HashiCorp Vault with External Secrets Operator, one key per client, scheduled rotation, short-lived OIDC credentials in CI. If a secret is committed: rotate first, then purge history.

## 4. Container and runtime hardening

Multi-stage `python:3.11-slim`; only runtime packages (`requirements-api.txt`);
non-root `appuser` uid 10001; code read-only for the app user; healthcheck
without curl. Compose and Kubernetes add a read-only root filesystem, dropped
Linux capabilities, `no-new-privileges` / `allowPrivilegeEscalation: false`,
seccomp `RuntimeDefault`, resource limits, a NetworkPolicy, and no mounted
service-account token. Writable paths are only `/tmp`, `/app/logs`,
`/app/data/inference`.

**Read-only filesystem guidance:** keep the root filesystem read-only; mount
writable volumes only where the app writes (audit log, inference log, drift
reports); mount `/tmp` with `noexec,nosuid`. If inference logging fails (e.g.
read-only mount) predictions still succeed and a warning is logged.

## 5. Scanning commands

```bash
make security-scan   # Bandit (-ll -ii), pip-audit on requirements-api.txt,
                     # Gitleaks, Trivy fs (CRITICAL), Checkov - the last three only if installed
make lint type-check
```

In CI (`security.yml`, `docker-publish.yml`): Bandit, pip-audit, Gitleaks (full
history), Checkov, CodeQL (`security-extended`), Trivy fs, Trivy image, SBOM,
cosign. Results are uploaded as SARIF to the Security tab.

## 6. Status and known gaps

- Locally verified: Bandit and pip-audit run through `make security-scan`; security tests pass.
- Not yet run: Gitleaks, Trivy, Checkov, CodeQL (not installed locally / workflows not run on GitHub), Docker image scan (Docker daemon not running).
- Known gaps (lab scope): static API keys instead of OIDC/JWT; audit log is a local file; rate limit is per process; no TLS inside the app (must be terminated by an ingress/load balancer); actions pinned to major versions, not SHAs.
