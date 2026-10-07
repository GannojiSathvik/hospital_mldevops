# Incident Response and Runtime Protection

> Educational project on synthetic data. Not for clinical use.

An **incident** is any event that hurts, or could hurt, the confidentiality
(who can see data), integrity (whether data and the model can be trusted) or
availability (whether the service is up) of the readmission API. This runbook
says what to do when one happens: how we notice it, how bad it is, and the
exact commands and files in this repo used to stop it, fix it and learn from
it. A **runbook** is a step-by-step procedure written in advance so nobody has
to improvise under pressure. Everything here refers to controls that already
exist in the repo; what is missing is listed in section 8. Related pages:
`docs/security.md` (controls), `docs/risk_assessment.md` (risks S1-S10,
O1-O4), `docs/monitoring.md` (drift), `SECURITY.md` (how outsiders report a
vulnerability).

## 1. Severity levels

**Severity** = how urgent the incident is. It decides who is involved and how
fast we must act.

| Level | Meaning | Examples in this system | Proposed response time |
|-------|---------|-------------------------|------------------------|
| **SEV1** (critical) | Service down for everyone, or confirmed exposure of keys/data | `ApiDown` firing; an `admin` or `analyst` key confirmed leaked and used; mass `/predict/batch` downloads by an unknown caller; a real secret pushed to a public repo | Start within 15 min, work until contained |
| **SEV2** (high) | Partial outage, or a security/model problem not yet confirmed as exploited | `HighErrorRate`; sustained `AuthFailureBurst`; a promoted model suspected bad or poisoned; CRITICAL CVE in the deployed image | Start within 4 hours |
| **SEV3** (low) | Degraded quality, no data or key exposure | `HighPredictionLatencyP95`; a single `HighRiskPredictionSpike` or drift report; a Dependabot PR for a non-critical CVE | Next working day |

Response times are a proposal for a real deployment; this lab has no on-call
rota. When unsure, pick the higher severity and downgrade later.

## 2. Lifecycle

The six phases below follow the common NIST-style incident lifecycle.

| Phase | Plain meaning |
|-------|---------------|
| **Detect** | Notice something is wrong (an alert, a scan, a user report). |
| **Triage** | Decide if it is real, how bad (SEV), and who owns it. |
| **Contain** | Stop the damage spreading *now*, even with a crude fix (revoke a key, scale down, roll back). |
| **Eradicate** | Remove the root cause (patch the dependency, purge the secret, retrain on clean data). |
| **Recover** | Return to normal service and watch closely that it stays normal. |
| **Post-incident review** | Write down what happened and what to change, without blame. |

### 2.1 Detect — the Prometheus alerts

**Prometheus** scrapes (periodically reads) the API's `GET /metrics` endpoint
and evaluates the rules in `infrastructure/monitoring/alert_rules.yml`. An
alert "fires" when its expression stays true for the `for:` duration. Firing
alerts are visible at `http://localhost:9090/alerts` and on the Grafana
dashboard "Healthcare Readmission API" (`docker compose up -d` / `make docker-up`).

| Alert (severity label) | Expression in plain words | Likely meaning | First action |
|------------------------|---------------------------|----------------|--------------|
| `ApiDown` (critical) | Prometheus could not scrape the `readmission-api` job for 2 min (`up == 0`) | Container/pod crashed, OOM-killed, or network/scrape config broken | `docker compose ps` / `kubectl -n healthcare-readmission get pods`; then read logs (2.2). SEV1. |
| `HighErrorRate` (warning) | More than 5% of requests returned 5xx over 5 min (`http_requests_total{status=~"5.."}`) | Model not loaded (503 from `/ready` and `/predict*`), a bug (generic 500), or a bad deploy | `curl -s localhost:8000/ready`; search logs for `"Unhandled error"`; if it started with a deploy, roll back (playbook d). SEV2. |
| `HighPredictionLatencyP95` (warning) | 95th-percentile model inference time above 0.5 s for 10 min (`prediction_latency_seconds`) | CPU saturation, many `/predict/explain` (SHAP is slow) or large batches, a heavier model | Check the "Requests by path and status" panel; check HPA: `kubectl -n healthcare-readmission get hpa`. SEV3. |
| `HighRiskPredictionSpike` (warning) | More than 50% of predictions are High risk over 1 h, for 30 min (`high_risk_predictions_total / predictions_total`) | Data drift, an upstream data bug (wrong units), or a bad/poisoned model | Run the drift check (playbook d, step 1). SEV3, raise to SEV2 if a new model was just promoted. |
| `AuthFailureBurst` (warning) | More than 10 rejected requests per minute for one `reason` for 5 min (`auth_failures_total{reason}`) | `invalid_key`: key guessing (brute force) or a client with a rotated key; `missing_key`: misconfigured client or scanner; `forbidden`: a valid key probing endpoints its role cannot use | Playbook b. SEV2. |

A **percentile (p95)** is the value below which 95% of measurements fall, so
p95 latency ignores the slowest 5% of outliers but still catches a general
slowdown. Other detection sources: weekly `Security` workflow (pip-audit,
Trivy, Gitleaks, CodeQL), Dependabot PRs, the daily drift workflow
(`drift-monitoring.yml`), and private reports via `SECURITY.md`.

### 2.2 Where the evidence is

| Evidence | Location | How to read it |
|----------|----------|----------------|
| API request log (JSON lines on stdout) | container output | `docker compose logs api` / `kubectl -n healthcare-readmission logs deployment/readmission-api` |
| Audit log (one JSON line per allow/deny decision) | `logs/audit.log` (`/app/logs/audit.log` in the container; path set by `AUDIT_LOG_PATH`) | `docker compose exec api grep ... /app/logs/audit.log` |
| Inference log (inputs + outputs of every prediction) | `data/inference/inference_log.csv` (`INFERENCE_LOG_PATH`) | used by drift monitoring |
| Metrics | `GET /metrics`, Prometheus, Grafana | see 2.1 |
| Model identity | `GET /model-info` (`model_version`, `training_data_hash`), `models/model_metadata.json` | needs a `model:read` key |
| Retraining decisions | `reports/retraining/decision.json` and `decision_<timestamp>.json`, `reports/drift/retrain_decision.json` | plain JSON |

**Preserve evidence before you restart anything.** In Kubernetes the audit
and inference logs live on `emptyDir` volumes (temporary disk that is deleted
when the pod is deleted), so a `rollout restart` destroys them. Copy them out
first, from **every** pod:

```bash
for p in $(kubectl -n healthcare-readmission get pods -l app.kubernetes.io/name=readmission-api -o name); do
  kubectl -n healthcare-readmission exec "$p" -- cat /app/logs/audit.log > "audit-${p#pod/}.log"
done
```

## 3. Playbooks

A **playbook** is the short runbook for one specific kind of incident.
Commands use the Kubernetes namespace `healthcare-readmission` and deployment
`readmission-api` from `infrastructure/kubernetes/`; the local equivalent uses
`docker compose` and `.env`.

### (a) Leaked API key (risk S1)

API keys come from the `API_KEYS` environment variable in the format
`name:role:key,name:role:key` (`src/security/rbac.py`). Only SHA-256 hashes
are kept in memory, and the audit log records the key's **name**, never the
key. A leaked key gives exactly the permissions of its role in
`configs/access_control.yaml`.

1. **Triage.** Identify the key's `name` and role (from `.env` / the Kubernetes Secret). An `admin` (`*`) or `analyst`/`ml_engineer` (batch export) key is SEV1; `viewer` is SEV3.
2. **Preserve evidence** (2.2) — copy every pod's `audit.log` before step 4.
3. **Rotate (contain).** Build a new `API_KEYS` value without the leaked key and with a fresh one (`python -c 'import secrets;print(secrets.token_urlsafe(32))'`, as in `infrastructure/kubernetes/secret.example.yaml`):
   ```bash
   kubectl -n healthcare-readmission create secret generic readmission-api-secrets \
     --from-literal=API_KEYS="svc-clinic:clinician:<new-key>,..." --dry-run=client -o yaml | kubectl apply -f -
   kubectl -n healthcare-readmission rollout restart deployment/readmission-api
   kubectl -n healthcare-readmission rollout status deployment/readmission-api
   ```
   Locally: edit `API_KEYS` in `.env`, then `docker compose up -d --force-recreate api`. The env var is read when the process starts, so a restart is required. If the key was also used for pipelines, update the GitHub secrets `API_KEYS` / `PIPELINE_API_KEY`.
4. **Verify revocation.** A request with the old key must now return 401: `curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: <old-key>" localhost:8000/whoami`.
5. **Scope the damage.** List everything that key's name did (replace `svc-clinic` with the key name):
   ```bash
   grep '"principal": "svc-clinic"' logs/audit.log
   grep '"principal": "svc-clinic"' logs/audit.log | grep '"allowed": true' | grep -c 'predict:batch'
   ```
   Look for unusual `client_ip` values, times, or `allowed: false` / `reason: forbidden` lines (probing other endpoints).
6. **Notify / review.** Hand the new key to the legitimate owner over a secure channel; record how it leaked.

### (b) Brute force / `AuthFailureBurst` (risk S1, S7)

**Brute force** = trying many keys until one works. Keys are random 32-byte
tokens compared with `hmac.compare_digest` (constant time, so timing reveals
nothing), which makes guessing impractical — but it must still be stopped.

1. **Triage by `reason`** (the alert label). `invalid_key` = guessing or a client still using a rotated key; `missing_key` = scanner or broken client; `forbidden` = a *valid* key probing endpoints — treat as possible leaked key (playbook a).
2. **Find the source:**
   ```bash
   grep '"reason": "invalid_key"' logs/audit.log | grep -o '"client_ip": "[^"]*"' | sort | uniq -c | sort -rn | head
   ```
3. **Contain.** The app has no IP block list. Block the source address at the edge (ingress controller or cloud firewall in front of the API), or, if it is a known internal client, fix/rotate its key.
4. **Important:** the in-app rate limit does **not** slow down this attack — see section 8 (failed-auth requests are never counted). Containment must happen outside the app.
5. **Recover.** Confirm the "Auth failures by reason" Grafana panel drops back to near zero.

### (c) Bulk data exfiltration via `/predict/batch` (risk S2)

**Exfiltration** = copying data out without permission. `/predict/batch`
returns a CSV of predictions for up to 10,000 rows per call
(`MAX_BATCH_ROWS`), 5 MB per upload (`MAX_UPLOAD_MB`), and needs
`predict:batch` (analyst, ml_engineer, admin only). Clinicians and viewers get
403.

1. **Detect.** Many `predict:batch` lines for one principal in a short time, or batch use outside working hours:
   ```bash
   grep '"action": "predict:batch"' logs/audit.log | grep '"allowed": true' | grep -o '"principal": "[^"]*"' | sort | uniq -c
   ```
   429 responses to that caller in the API log mean it hit `RATE_LIMIT` (default 60/minute per key).
2. **Contain.** Treat the caller's key as compromised: rotate it (playbook a, step 3). For a whole role, temporarily remove `predict:batch` from that role in `configs/access_control.yaml` and redeploy (the policy file is baked into the image and cached at start).
3. **Scope.** The audit log tells you *who* and *when*, not *how many rows*. The inference log `data/inference/inference_log.csv` holds the inputs and `timestamp` of every scored row (it does not store `patient_id`); match its timestamps to the audit entries to estimate volume.
4. **Notify.** In a real deployment this is a potential data breach: involve the privacy/compliance owner. (Here all data is synthetic.)

### (d) Bad or poisoned model / drift (risks C5, S3, O3)

**Poisoning** = deliberately corrupting training data so the model learns
wrong behaviour. **Drift** = production data no longer looks like training
data (`docs/monitoring.md`). Both show up as `HighRiskPredictionSpike`, odd
metrics, or a drift report.

1. **Confirm.** Run drift detection: `make monitor` (or `python -m pipelines.monitoring_pipeline`) and open the newest `reports/drift/drift_report_*.html`; or `POST /monitor/drift` with an analyst key. Check `GET /model-info` for `model_version`, `trained_at`, `training_data_hash`.
2. **Was a new model just promoted?** Read `reports/retraining/decision.json` (`action: promoted`, `archived_to`, champion vs challenger PR-AUC). **Champion/challenger** = the current production model (champion) is replaced by the new one (challenger) only if the challenger's PR-AUC on the same test set is not worse.
3. **Contain — roll back.** The API loads the model from `models/` *inside the image* (see `Dockerfile`), so a model rollback is an image rollback:
   ```bash
   kubectl -n healthcare-readmission rollout history deployment/readmission-api
   kubectl -n healthcare-readmission rollout undo deployment/readmission-api   # previous ReplicaSet (revisionHistoryLimit: 3)
   ```
   Or redeploy a known-good image by digest through the `Deploy` workflow: `gh workflow run deploy.yml -f image_digest=sha256:<good-digest>` (still requires production approval).
4. **Fix the artifacts.** Restore the previous champion from `models/archive/<timestamp>/` (written by `pipelines/retraining_pipeline.py` before every promotion) into `models/`, then rebuild and publish through CI. To identify the matching MLflow Model Registry version of `healthcare-readmission`, open MLflow (`http://localhost:5000`) and compare each version's `training_data_hash` and `trained_by` tags (set by `scripts/register_model.py`) with `models/model_metadata.json`.
5. **Eradicate.** Find the bad data (`data/inference/labeled_feedback.csv` is merged into training during retraining), remove it, and retrain: `make retrain` (needs an `ml_engineer`/`admin` `PIPELINE_API_KEY`). Who triggered training is in `trained_by` and in `logs/audit.log` (`resource: "pipeline"`).
6. **Recover.** Review the automated retraining PR (`retraining.yml` opens one; merging does not deploy) before promoting again.

### (e) Vulnerable dependency or image CVE (risk S5)

A **CVE** (Common Vulnerabilities and Exposures) is a public ID for a known
security bug in a package or OS library.

1. **Detect.** pip-audit job in `security.yml` (runs on push/PR and weekly), Trivy gate in `docker-publish.yml` (fails on CRITICAL), Trivy fs in `security.yml`, Dependabot PRs (`.github/dependabot.yml`, weekly for pip, Actions and Docker).
2. **Reproduce locally:** `make security-scan` (Bandit + `pip-audit -r requirements-api.txt`, plus Gitleaks/Trivy/Checkov if installed), or for the built image: `trivy image --severity CRITICAL healthcare-readmission-api:latest`.
3. **Triage.** Is the package in `requirements-api.txt` (shipped in the image) or only in `requirements-dev.txt` (tooling)? Is the vulnerable function reachable from the API? CRITICAL in the running image = SEV2.
4. **Eradicate.** Bump the pinned version in `requirements-api.txt` (or merge the Dependabot PR), or update the `python:3.11-slim` base in `Dockerfile` for OS-level CVEs; run `make test` and `make security-scan`.
5. **Recover.** Merge to `main` → `Docker Publish` builds, Trivy-gates, signs → `Deploy` rolls out (production after approval).

### (f) Secret committed to git (risk S4)

Order matters: **rotate first, then purge history.** Deleting the file in a new
commit is not enough — the secret stays in every clone and in git history.

1. **Detect.** Gitleaks in pre-commit (`.pre-commit-config.yaml`) and in CI over full history: `gitleaks git --config .gitleaks.toml --redact --verbose --exit-code 1 .`
2. **Rotate immediately** — assume it is already copied. API key: playbook a, step 3. GitHub secrets (`API_KEYS`, `PIPELINE_API_KEY`, `KUBE_CONFIG_*`) or `GRAFANA_ADMIN_PASSWORD`: generate new values and update them where they live.
3. **Check use.** For API keys, grep `logs/audit.log` for the key's name (playbook a, step 5).
4. **Purge history.** Rewrite history with a tool such as `git filter-repo` (not part of this repo), force-push, and ask collaborators to re-clone; ask GitHub support to purge cached views if the repo is public.
5. **Prevent.** Make sure the developer has run `make pre-commit`; never put real values in `.env.example`.

## 4. Tracing one request end to end

Every response carries an `X-Request-ID` header (`api/middleware.py`). The
client can send its own ID if it matches `^[A-Za-z0-9._-]{1,64}$` (this
rejects newlines and quotes, preventing **log injection** — fake log lines
smuggled inside a header); otherwise a random UUID is generated. The same ID
appears in the API log, the audit log, and the JSON body of 422/429/500/503
errors, so one ID links what the user saw to what the server recorded.

```bash
curl -s -D - -H "X-API-Key: $KEY" -H "X-Request-ID: demo-123" \
     -H "Content-Type: application/json" -d @patient.json localhost:8000/predict
```

Example lines (field layout captured from the running code; values illustrative).
API log line (stdout, format from `configs/logging.yaml` + the middleware):

```json
{"timestamp": "2026-10-07 10:32:40,159", "level": "INFO", "logger": "api.middleware", "message": "request", "request_id": "demo-123", "method": "POST", "path": "/predict", "status": 200, "duration_ms": 0.47}
```

Audit log line (`logs/audit.log`, written by `audit_log()` via `api/security.py`):

```json
{"timestamp": "2026-10-07T05:02:40.163084+00:00", "principal": "demo-analyst", "role": "analyst", "action": "predict:single", "resource": "POST /predict", "allowed": true, "request_id": "demo-123", "client_ip": "127.0.0.1"}
```

Then:

```bash
docker compose logs api | grep '"request_id": "demo-123"'
grep '"request_id": "demo-123"' logs/audit.log
```

How to read the pair: the audit line answers *who* and *was it allowed*; the
API log answers *what status came back and how long it took*. For an
unexpected error the API log also has an `"Unhandled error on ..."` line with
the stack trace, while the client only got
`{"detail": "Internal server error", "request_id": "demo-123"}`. Note that the
audit log says `allowed: true` as soon as authorisation passes, so a request
later rejected with 422 or 429 still shows `allowed: true` there — the API log
has the final status.

## 5. Runtime protection (RASP)

**RASP** (Runtime Application Self-Protection) is a security agent that runs
*inside* the application process and blocks attacks (e.g. SQL injection,
command injection) as they happen. A **WAF** (Web Application Firewall) does
something similar *in front of* the application, filtering HTTP traffic.

**This project has no dedicated RASP product and no WAF.** Instead, the
following controls run inside the app or its container on every request and
serve the same purpose — refusing malicious or abusive input at runtime:

| Runtime control | Where | What the attacker gets |
|-----------------|-------|------------------------|
| Authentication on every protected call (`X-API-Key`) + RBAC | `api/security.py`, `configs/access_control.yaml` | **401** (no/unknown key) or **403** (role lacks permission); every decision audited, failures counted in `auth_failures_total` |
| Strict input validation (ranges, allowed categories, unknown fields rejected) | Pydantic models in `api/schemas.py` | **422** listing field + message only, submitted values not echoed |
| Batch CSV checks (extension, content type, columns, ranges, categories) | `api/main.py` | **400** with a short reason |
| Upload and row caps (`MAX_UPLOAD_MB` 5, `MAX_BATCH_ROWS` 10,000); at most limit+1 bytes read | `api/main.py` | **413**, memory cannot be exhausted by a huge file |
| Rate limiting (`RATE_LIMIT`, default `60/minute`, per hashed key or IP) on `/predict`, `/predict/explain`, `/predict/batch` | slowapi in `api/main.py` | **429** |
| Generic errors (no stack traces, paths or versions) | `api/middleware.py` | **500** `Internal server error` + request ID |
| Secure headers: `nosniff`, `X-Frame-Options: DENY`, strict `Content-Security-Policy`, HSTS, `Cache-Control: no-store`, `Referrer-Policy: no-referrer` | `api/middleware.py` | Browser refuses framing, script injection from other origins, caching of patient results |
| Safe request IDs (regex) | `api/middleware.py` | Cannot inject fake log lines |
| `/docs`, `/redoc`, `/openapi.json` off when `ENV=production` | `api/main.py`, `configmap.yaml` | No free map of the API |
| Model files only loaded from `models/`, never from uploads (pickle can run code) | `api/prediction_service.py` | Cannot upload a malicious model |
| Non-root user (uid 10001), read-only root filesystem, all Linux capabilities dropped, no privilege escalation, seccomp `RuntimeDefault`, CPU/memory limits | `Dockerfile`, `docker-compose.yml`, `infrastructure/kubernetes/deployment.yaml` | Even if code execution is achieved, the attacker cannot write to the app or escalate |
| Default-deny NetworkPolicy, egress only DNS | `infrastructure/kubernetes/networkpolicy.yaml` | A compromised pod cannot call out to an attacker's server |

What a real RASP/WAF would add: blocking or throttling a source IP
automatically after repeated failures; detection of attack payloads across
all endpoints (not just schema checks); virtual patching (blocking a known CVE
exploit pattern before the dependency is upgraded); bot detection; a
centralised view of attacks across all replicas. Options: a cloud WAF
(e.g. AWS WAF in front of the load balancer) or ModSecurity rules on the
ingress-nginx controller that the NetworkPolicy already expects.

## 6. Post-incident review template

Write within 5 working days, blameless (focus on the system, not the person):

1. Summary (one paragraph) and SEV level.
2. Timeline with UTC timestamps (detection, triage, containment, recovery) — use `logs/audit.log` timestamps and request IDs.
3. Root cause and why existing controls did not stop it.
4. Impact: which keys/roles, how many requests (audit log), data/model affected.
5. Action items with owners — e.g. a new alert rule in `alert_rules.yml`, a new test in `tests/security/`, an update to `docs/risk_assessment.md`.

## 7. Quick reference

| Task | Command |
|------|---------|
| Is it up / model loaded? | `curl -s localhost:8000/health` · `curl -s localhost:8000/ready` |
| Pods and recent rollouts | `kubectl -n healthcare-readmission get pods` · `kubectl -n healthcare-readmission rollout history deployment/readmission-api` |
| Roll back image | `kubectl -n healthcare-readmission rollout undo deployment/readmission-api` |
| Deploy a specific image | `gh workflow run deploy.yml -f image_digest=sha256:<digest>` |
| Restart after secret change | `kubectl -n healthcare-readmission rollout restart deployment/readmission-api` |
| What did principal X do? | `grep '"principal": "X"' logs/audit.log` |
| Trace a request | `grep '"request_id": "<id>"' logs/audit.log` + API log |
| Drift check | `make monitor` |
| Scans | `make security-scan` |

## 8. Known gaps

Consistent with `docs/security.md` section 6, plus gaps found while writing this runbook:

- **Audit log is a local file** per container (`logs/audit.log`, on `emptyDir` in Kubernetes): lost on pod restart, editable by anyone with shell access, and split across replicas. Needs shipping to central, append-only storage (risk S10).
- **Static API keys** with no expiry; rotation requires a restart, and there is no per-key revoke without redeploying the full `API_KEYS` value.
- **Rate limit is per process** (slowapi in-memory storage): with 2-6 replicas the effective limit is multiplied by the replica count, and counters reset on restart.
- **Failed-auth requests are not rate-limited.** FastAPI runs the auth dependency before the slowapi-decorated handler, so 401/403 (and 422) responses are never counted toward `RATE_LIMIT`; `/whoami` and `/model-info` have no rate limit at all. Brute force is only *detected* (`AuthFailureBurst`), not throttled, in-app. There is also no automated test for the 429 path (`tests/conftest.py` sets `RATE_LIMIT=10000/minute`).
- **No alert routing.** There is no Alertmanager configuration, so alerts appear only in the Prometheus UI and Grafana; nobody is paged. The `job="readmission-api"` scrape config exists only in `prometheus.yml` for docker compose, not for the Kubernetes deployment.
- **Audit log has no data volume.** It does not record batch row counts or patient IDs, so the size of an exfiltration must be estimated from the inference log timestamps.
- **The API does not serve from the MLflow registry.** Models are baked into the image from `models/`; registry versions are an audit record, and rollback means redeploying an older image or rebuilding from `models/archive/`.
- **No IP block list, WAF or RASP**, and no TLS inside the app (must be terminated at the ingress).
- **No automated containment** (e.g. auto-disabling a key after N failures) and no on-call rota.
