# Security Policy

> Educational project. The model and API are **not** a medical device and must
> not be used for real clinical decisions. All data is synthetic.

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` / latest release | Yes |
| older tags | No |

## Reporting a vulnerability

**Do not open a public issue for an undisclosed vulnerability.**

1. Use GitHub **Security -> Report a vulnerability** (private security advisory).
2. Include: affected component, steps to reproduce, impact, and a suggested fix if you have one.
3. Never include real patient data or live credentials in a report.

Response targets: acknowledgement within 3 business days, triage within 7 days,
fix for Critical/High issues within 30 days.

## Security controls in this repository

| Area | Control |
|------|---------|
| Secrets | Read from environment variables only (`.env` locally, GitHub/Kubernetes secrets in CI/CD). `.env` is git-ignored; `.env.example` holds obviously fake demo keys. |
| Secret scanning | Gitleaks in pre-commit and CI (`security.yml`), full git history. |
| Authentication | `X-API-Key` header; keys stored only as SHA-256 hashes in memory, compared with `hmac.compare_digest`. |
| Authorisation (Lab 6) | Role-based access control: `configs/access_control.yaml` (viewer, clinician, analyst, ml_engineer, admin). Pipelines check `PIPELINE_API_KEY` before training/retraining. Every decision is written to `logs/audit.log`. |
| Static analysis | Bandit (Python SAST), CodeQL (data-flow analysis). |
| Dependencies | Pinned versions, pip-audit, Trivy, Dependabot weekly updates. |
| Container | Multi-stage `python:3.11-slim`, non-root `appuser` (uid 10001), only runtime deps, read-only root filesystem in compose/K8s, all Linux capabilities dropped, `no-new-privileges`. |
| Supply chain | Trivy image scan fails on CRITICAL CVEs, SPDX SBOM, cosign keyless signature, GHCR publish only from `main`/tags. |
| IaC | Checkov on Terraform, Kubernetes, Dockerfile and GitHub workflows. |
| CI/CD | Least-privilege `GITHUB_TOKEN` (`contents: read` default), environment-scoped secrets, manual approval before production. |
| API hardening | Input validation (Pydantic), upload size limit, rate limiting, secure HTTP headers, generic error messages (no stack traces). |

## Secret-management recommendations for real deployments

- Use a managed secret store (AWS Secrets Manager, HashiCorp Vault, or Kubernetes
  Secrets encrypted at rest with KMS + External Secrets Operator).
- Rotate API keys regularly; issue one key per client/service so a key can be revoked alone.
- Prefer short-lived OIDC credentials in CI over long-lived cloud keys.
- If a secret is ever committed: revoke/rotate it first, then purge history — deleting the file is not enough.
