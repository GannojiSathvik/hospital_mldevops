# Deployment Guide

> Educational project on synthetic data. Not for clinical use.

**Validation status:** the Docker build, Compose stack, Kubernetes manifests,
Terraform and GitHub deploy workflow have **not** been executed yet (Docker
daemon not running, tools not installed, repository not pushed). The steps
below describe what the files are written to do.

## 1. Prerequisites

1. Trained artifacts in `models/` — the image copies them: `make train`.
2. A `.env` file: `cp .env.example .env`, then replace every demo value. Generate keys with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## 2. Local: plain Python

```bash
make run-api      # 127.0.0.1:8000, auto-reload, ENV from .env
```

Different port: `set -a; source .env; set +a; .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8001`.

## 3. Local: Docker / Docker Compose

```bash
make docker-build     # image healthcare-readmission-api:latest
make docker-up        # api :8000, mlflow :5000, prometheus :9090, grafana :3000
make docker-down
```

What the image does (`Dockerfile`):

- **Multi-stage build** — dependencies installed in a `builder` stage; the `runtime` stage copies only the virtualenv and app code. Fewer packages means fewer CVEs.
- **Non-root** user `appuser` (uid/gid 10001); code owned by root and read-only for the app.
- Copies only `api/`, `src/`, `configs/`, `models/`, `data/processed/train.csv` (drift reference).
- `ENV=production` by default, so `/docs` is off.
- `HEALTHCHECK` calls `/health` with the Python standard library (no curl in the image).
- No secrets baked in.

Compose hardening for the API: `read_only: true` root filesystem, `tmpfs /tmp`
(noexec, nosuid), named volumes only for `/app/logs` and `/app/data/inference`,
`cap_drop: [ALL]`, `no-new-privileges`, `pids_limit: 256`, `mem_limit: 1g`,
`cpus: 1.0`. Compose sets `ENV=${ENV:-development}`, so `/docs` stays available
locally. Grafana refuses to start unless `GRAFANA_ADMIN_PASSWORD` is set.

Port clash: change the host side of `"8000:8000"` in `docker-compose.yml`.

## 4. Kubernetes

```bash
kubectl create secret generic readmission-api-secrets \
  -n healthcare-readmission --from-literal=API_KEYS='name:role:key,...'
kubectl apply -k infrastructure/kubernetes
```

`kustomization.yaml` deliberately excludes `secret.example.yaml`; create the
secret out of band (or via External Secrets Operator). Manifests:

| File | Purpose |
|------|---------|
| `namespace.yaml` | `healthcare-readmission` namespace |
| `deployment.yaml` | 2 replicas, rolling update with `maxUnavailable: 0`; `runAsNonRoot`, uid 10001, `readOnlyRootFilesystem`, all capabilities dropped, seccomp `RuntimeDefault`, no service-account token; CPU/memory requests and limits; liveness `/health`, readiness `/ready`; writable `emptyDir` only for `/tmp`, `/app/logs`, `/app/data/inference` |
| `service.yaml`, `hpa.yaml` | Service; autoscale 2-6 replicas at 70% CPU |
| `networkpolicy.yaml` | Default-deny; ingress only from the ingress controller and Prometheus on port 8000; egress limited (DNS on 53) |
| `serviceaccount.yaml`, `rbac.yaml` | Dedicated service account; Kubernetes Role allows only `get` on its own ConfigMap (cluster-level RBAC, complementing the app-level Lab 6 RBAC) |
| `configmap.yaml` | Non-secret settings |

Note: inference logs on `emptyDir` disappear with the pod; in production ship
them to object storage or a persistent volume for drift jobs.

## 5. Cloud registry and storage (Terraform example)

`infrastructure/terraform/` defines an AWS example: KMS key with rotation, ECR
repository (immutable tags, scan on push, KMS encryption) with a lifecycle
policy, and S3 buckets for model artifacts and access logs (versioning,
encryption, public access blocked, lifecycle rules, bucket policies).

```bash
cd infrastructure/terraform
terraform init && terraform plan
```

## 6. CI/CD deployment (`.github/workflows/deploy.yml`)

1. `docker-publish.yml` on `main`: build -> Trivy image scan (fail on CRITICAL) -> SBOM -> push to GHCR -> cosign keyless signature.
2. `deploy.yml` runs after a successful publish: **staging** deploys automatically (`kubectl apply -k`, `set image` to the `sha-<commit>` tag, or to a `sha256` digest when started manually, `rollout status`).
3. **production** declares `environment: production`; GitHub pauses until a required reviewer approves. It needs staging to have succeeded.
4. Without `KUBE_CONFIG_STAGING` / `KUBE_CONFIG_PRODUCTION` secrets the jobs run in "example mode" and only print the `kubectl` command.

Setup and approval rules: `docs/ci_cd_pipeline.md` section 5.

## 7. Production checklist

- [ ] Real keys in a secrets manager, one per client, rotation scheduled
- [ ] TLS terminated in front of the API (ingress / load balancer)
- [ ] `ENV=production` (docs disabled)
- [ ] Read-only root filesystem, non-root, capabilities dropped
- [ ] Image referenced by digest and signature verified
- [ ] Audit and inference logs shipped to central storage
- [ ] Prometheus alerts (`ApiDown`, `HighErrorRate`, `HighPredictionLatencyP95`, `HighRiskPredictionSpike`, `AuthFailureBurst`) routed to on-call
- [ ] Manual approval enabled on the `production` environment
- [ ] Rollback plan: `kubectl rollout undo deployment/readmission-api -n healthcare-readmission`; previous model artifacts in `models/archive/` after a retraining promotion
